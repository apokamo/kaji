#!/usr/bin/env bash
#
# inventory_cli_main_patch_targets.sh
#
# tests/ 内から `kaji_harness.cli_main.<symbol>` を差し替える patch / monkeypatch /
# patch.object / helper・fixture を全件抽出し、機械決定可能な列のみを決定的順序で
# stdout に出力する（Issue #282 R0 の棚卸し再現スクリプト）。
#
# 自動生成する列（機械決定可能・再実行で再現）:
#   test_file  line  test_name  target_symbol  reference_form  symbol_origin
#
# 自動生成しない列（人手判断。committed 表に手で埋める）:
#   参照元関数 / re-export 可否 / R1 移行先候補 / 対応方針
#
# 契約:
#   - 出力は (test_file, line) ソートの決定的順序
#   - 正常終了で exit 0
#   - 出力を baseline（Issue コメント + committed 表）と diff し差分ゼロ = 棚卸し再現成功
#
# 走査する参照形態（設計 3-a 表）:
#   module 経由: 文字列 target      patch("kaji_harness.cli_main.<sym>") / mocker.patch(...)
#   module 経由: monkeypatch/属性代入 monkeypatch.setattr("kaji_harness.cli_main.<sym>", ...) /
#                                     monkeypatch.setattr(cli_main, "<sym>", ...) / cli_main.<sym> = ...
#   直接シンボル: patch.object       patch.object(<cli_main 由来 import>, "attr")
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_MAIN="${REPO_ROOT}/kaji_harness/cli_main.py"
TESTS_DIR="${REPO_ROOT}/tests"

if [[ ! -f "${CLI_MAIN}" ]]; then
  echo "error: ${CLI_MAIN} not found" >&2
  exit 1
fi

# cli_main.py を先に読み symbol_origin（import 束縛 / cli_main 内定義）を確定した上で、
# tests 配下の該当行を抽出する。FILENAME が cli_main.py のパスと一致する pass で
# origin[] を構築し、以降の test file pass で行を emit する。
# gawk へは cli_main.py を先頭引数、続けて決定的順序の test file 群を渡す。
mapfile -t TEST_FILES < <(find "${TESTS_DIR}" -name '*.py' -type f | LC_ALL=C sort)

gawk -v climain="${CLI_MAIN}" -v repo="${REPO_ROOT}/" '
BEGIN { OFS="\t"; in_import = 0 }

# ---- pass 1: cli_main.py から symbol_origin を構築 ----
FILENAME == climain {
  line = $0

  # トップレベル def / async def -> cli_main 内定義
  if (match(line, /^(def|async def)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)/, m)) {
    origin[m[2]] = "cli_main 内定義"
  }

  # import 継続（括弧内）
  if (in_import) {
    record_imports(line)
    if (line ~ /\)/) in_import = 0
    next
  }
  # import X / import X, Y
  if (match(line, /^import[[:space:]]+(.+)$/, m)) {
    record_imports(m[1])
    next
  }
  # from M import ...
  if (match(line, /^from[[:space:]]+[A-Za-z_.][A-Za-z0-9_.]*[[:space:]]+import[[:space:]]+(.+)$/, m)) {
    rest = m[1]
    if (rest ~ /\(/ && rest !~ /\)/) in_import = 1
    gsub(/[()]/, "", rest)
    record_imports(rest)
    next
  }
  next
}

# ---- pass 2: test files から patch target を抽出 ----
{
  # 相対パス（repo root 起点）
  rel = FILENAME
  sub(repo, "", rel)

  # 直近の enclosing def / async def（test 名 or fixture 名）を追跡
  if (match($0, /^[[:space:]]*(def|async def)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)/, dm)) {
    curfn[FILENAME] = dm[2]
  }

  # この test file が cli_main から直接 import している名前を追跡（直接シンボル判定用）。
  #   from kaji_harness.cli_main import a, b      -> a, b を cli_main 由来として記録
  #   from kaji_harness import cli_main           -> module alias cli_main を記録
  #   import kaji_harness.cli_main as X           -> module alias X を記録
  if (match($0, /^[[:space:]]*from[[:space:]]+kaji_harness\.cli_main[[:space:]]+import[[:space:]]+(.+)$/, im)) {
    reg = im[1]
    if (reg ~ /\(/ && reg !~ /\)/) cm_in_import[FILENAME] = 1
    gsub(/[()]/, "", reg)
    register_cm_imports(FILENAME, reg)
  } else if (FILENAME in cm_in_import && cm_in_import[FILENAME] == 1) {
    reg = $0
    if (reg ~ /\)/) cm_in_import[FILENAME] = 0
    gsub(/[()]/, "", reg)
    register_cm_imports(FILENAME, reg)
  }
  if ($0 ~ /^[[:space:]]*from[[:space:]]+kaji_harness[[:space:]]+import[[:space:]]+cli_main([[:space:]]|$)/) {
    cm_alias[FILENAME "\034" "cli_main"] = 1
  }
  if (match($0, /^[[:space:]]*import[[:space:]]+kaji_harness\.cli_main[[:space:]]+as[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)/, am2)) {
    cm_alias[FILENAME "\034" am2[1]] = 1
  }

  fn = (FILENAME in curfn) ? curfn[FILENAME] : "<module>"

  # 参照形態 A: module 経由（文字列 target）
  #   patch / mocker.patch / monkeypatch.setattr|delattr の引数に "kaji_harness.cli_main.<sym>"。
  #   多行 patch 呼び出し（target 文字列が継続行にある）を漏らさないため、patch キーワードの
  #   同一行存在は要求せず、クオート付き dotted literal を直接検出する。tests 内で
  #   "kaji_harness.cli_main.<identifier>" 形のクオート文字列は patch/monkeypatch target のみ
  #   （import は `from kaji_harness.cli_main import ...` のクオート無し形を使う）。
  if (match($0, /["'"'"']kaji_harness\.cli_main\.([A-Za-z_][A-Za-z0-9_]*)/, sm)) {
    emit(rel, FNR, fn, sm[1], "module 経由")
    next
  }

  # 参照形態 A(2): monkeypatch.setattr(cli_main, "<sym>", ...) / cli_main.<sym> = ...
  if (match($0, /monkeypatch\.(setattr|delattr)\([[:space:]]*(kaji_harness\.)?cli_main[[:space:]]*,[[:space:]]*["'"'"']([A-Za-z_][A-Za-z0-9_]*)/, om)) {
    emit(rel, FNR, fn, om[3], "module 経由")
    next
  }
  if (match($0, /(^|[^.A-Za-z0-9_])cli_main\.([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[^=]/, am)) {
    emit(rel, FNR, fn, am[2], "module 経由")
    next
  }

  # 参照形態 B: 直接シンボル
  #   patch.object(cli_main.<sym>, ...) : module alias 経由の identity 参照
  if (match($0, /patch\.object\([[:space:]]*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*,/, qm)) {
    if ((FILENAME "\034" qm[1]) in cm_alias) {
      emit(rel, FNR, fn, qm[2], "直接シンボル")
      next
    }
  }
  #   patch.object(<name>, ...) : <name> を cli_main から直接 import している場合のみ
  if (match($0, /patch\.object\([[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*,/, pm)) {
    obj = pm[1]
    if ((FILENAME "\034" obj) in cm_imported) {
      emit(rel, FNR, fn, obj, "直接シンボル")
      next
    }
  }
}

# --- helpers ---
function record_imports(s,    parts, i, tok, name) {
  gsub(/,/, " ", s)
  n = split(s, parts, /[[:space:]]+/)
  for (i = 1; i <= n; i++) {
    tok = parts[i]
    if (tok == "" || tok == "as" || tok == "(" || tok == ")") continue
    # alias 対応: "X as Y" -> bound name は Y。ここでは順次処理し "as" の次を採用。
    if (i >= 3 && parts[i-1] == "as") { name = tok } else { name = tok }
    gsub(/[()]/, "", name)
    if (name ~ /^[A-Za-z_][A-Za-z0-9_]*$/) {
      # def で既に登録済みでなければ import 束縛として記録
      if (!(name in origin)) origin[name] = "import 束縛"
    }
  }
}

function register_cm_imports(file, s,    parts, i, tok) {
  gsub(/,/, " ", s)
  n2 = split(s, parts, /[[:space:]]+/)
  for (i = 1; i <= n2; i++) {
    tok = parts[i]
    if (tok == "" || tok == "as" || tok == "import") continue
    # "X as Y" は bound name Y を採る
    if (i >= 2 && parts[i-1] == "as") { cm_imported[file "\034" tok] = 1; continue }
    if (i < n2 && parts[i+1] == "as") continue
    if (tok ~ /^[A-Za-z_][A-Za-z0-9_]*$/) cm_imported[file "\034" tok] = 1
  }
}

function emit(rel, ln, fn, sym, form,    orig) {
  orig = (sym in origin) ? origin[sym] : "unknown"
  # (test_file, line) ソート用にキー付きで一旦バッファ
  key = sprintf("%s\t%09d", rel, ln)
  rows[key] = rel OFS ln OFS fn OFS sym OFS form OFS orig
}

END {
  print "test_file" OFS "line" OFS "test_name" OFS "target_symbol" OFS "reference_form" OFS "symbol_origin"
  n = asorti(rows, sorted)
  for (i = 1; i <= n; i++) print rows[sorted[i]]
}
' "${CLI_MAIN}" "${TEST_FILES[@]}"
