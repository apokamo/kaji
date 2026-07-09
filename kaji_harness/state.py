"""Session state management for kaji_harness.

Issue-scoped state that persists across workflow executions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .models import Verdict

STATE_FILE = "session-state.json"


def _format_issue_ref(issue: str | int) -> str:
    """Issue ID を人間可読な参照形式に整形する。

    数値のみ（GitHub Issue 番号）→ ``#153``。
    それ以外（local-pc1-1 等）→ そのまま返す。

    Phase 1 では provider 抽象が未導入のため、形式から判別する。
    int も受け付ける（既存呼び出し互換のための境界正規化）。
    """
    s = str(issue)
    return f"#{s}" if s.isdigit() else s


@dataclass
class StepRecord:
    """ステップ実行記録。

    Issue #222: ``attempt`` / ``exit_code`` / ``signal`` は optional（default
    ``None``）。旧 ``session-state.json``（これらのキーを持たない）の load
    （``StepRecord(**r)``）が壊れないよう末尾に追加する。dispatch を伴う step では
    ``attempt`` に整数が入り、異常終了では ``exit_code`` / ``signal`` が残る。
    """

    step_id: str
    verdict_status: str
    verdict_reason: str
    verdict_evidence: str
    verdict_suggestion: str
    timestamp: str
    attempt: int | None = None
    exit_code: int | None = None
    signal: str | None = None


@dataclass
class SessionState:
    """Issue 単位のセッション状態。"""

    issue_number: str
    artifacts_dir: Path
    sessions: dict[str, str] = field(default_factory=dict)
    step_history: list[StepRecord] = field(default_factory=list)
    cycle_counts: dict[str, int] = field(default_factory=dict)
    last_completed_step: str | None = None
    last_transition_verdict: Verdict | None = None
    # Issue #218: 初めて物理的に存在を確認した worktree/branch を構造化保存する。
    # mutable label からの再合成を避けるため、確定後は state を正本として override する。
    worktree_dir: str | None = None
    branch_name: str | None = None

    def __post_init__(self) -> None:
        # 既存呼び出しが int を渡してきても受理する（境界で str に正規化）
        self.issue_number = str(self.issue_number)

    @classmethod
    def load_or_create(cls, issue: str, artifacts_dir: Path) -> SessionState:
        """状態をロードまたは新規作成する。"""
        path = artifacts_dir / str(issue) / STATE_FILE
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            data["step_history"] = [StepRecord(**r) for r in data.get("step_history", [])]
            ltv = data.pop("last_transition_verdict", None)
            if ltv:
                data["last_transition_verdict"] = Verdict(**ltv)
            # 旧 cache 互換: issue_number が int で保存されていた場合も str 化して読み込む
            if "issue_number" in data and not isinstance(data["issue_number"], str):
                data["issue_number"] = str(data["issue_number"])
            return cls(artifacts_dir=artifacts_dir, **data)
        return cls(issue_number=str(issue), artifacts_dir=artifacts_dir)

    @property
    def _state_dir(self) -> Path:
        return self.artifacts_dir / self.issue_number

    def save_session_id(self, step_id: str, session_id: str) -> None:
        """ステップのセッション ID を保存し、即時永続化する。"""
        self.sessions[step_id] = session_id
        self._persist()

    def get_session_id(self, resume_target: str | None) -> str | None:
        """resume 対象のセッション ID を取得する。"""
        if resume_target is None:
            return None
        return self.sessions.get(resume_target)

    def cycle_iterations(self, cycle_name: str) -> int:
        """サイクルのイテレーション回数を取得する。"""
        return self.cycle_counts.get(cycle_name, 0)

    def increment_cycle(self, cycle_name: str) -> None:
        """サイクルのイテレーション回数をインクリメントし、即時永続化する。"""
        self.cycle_counts[cycle_name] = self.cycle_iterations(cycle_name) + 1
        self._persist()

    def reset_cycle(self, cycle_name: str) -> None:
        """指定サイクルの反復回数を 0 に戻し、即時永続化する。"""
        self.cycle_counts[cycle_name] = 0
        self._persist()

    def capture_worktree(self, worktree_dir: str, branch_name: str) -> None:
        """worktree/branch を構造化保存する（冪等。既に保存済みなら no-op）。

        Issue #218: mutable label からの再合成を避けるため、
        ``issue-start`` が確定した worktree/branch を 1 度だけ state に焼き込む。
        """
        if self.worktree_dir is not None and self.branch_name is not None:
            return
        self.worktree_dir = worktree_dir
        self.branch_name = branch_name
        self._persist()

    def record_step(
        self,
        step_id: str,
        verdict: Verdict,
        *,
        attempt: int | None = None,
        exit_code: int | None = None,
        signal: str | None = None,
    ) -> None:
        """ステップ実行結果を記録し、永続化する。

        Issue #222: ``attempt`` / ``exit_code`` / ``signal`` を受け取り、failed /
        aborted attempt を含めて ``progress.md`` に可視化する。dispatch を伴わない
        合成 verdict（cycle 上限 exhaust 等）では既定の ``None`` で呼ばれる。
        """
        self.step_history.append(
            StepRecord(
                step_id=step_id,
                verdict_status=verdict.status,
                verdict_reason=verdict.reason,
                verdict_evidence=verdict.evidence,
                verdict_suggestion=verdict.suggestion,
                timestamp=datetime.now(UTC).isoformat(),
                attempt=attempt,
                exit_code=exit_code,
                signal=signal,
            )
        )
        self.last_completed_step = step_id
        self.last_transition_verdict = verdict
        self._persist()

    def _persist(self) -> None:
        """JSON + progress.md に永続化する。"""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / STATE_FILE
        data = {
            "issue_number": self.issue_number,
            "sessions": self.sessions,
            "step_history": [asdict(r) for r in self.step_history],
            "cycle_counts": self.cycle_counts,
            "last_completed_step": self.last_completed_step,
            "last_transition_verdict": asdict(self.last_transition_verdict)
            if self.last_transition_verdict
            else None,
            "worktree_dir": self.worktree_dir,
            "branch_name": self.branch_name,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_progress_md()

    def _write_progress_md(self) -> None:
        """人間可読な進捗ファイルを更新する。"""
        lines = [f"# Progress: Issue {_format_issue_ref(self.issue_number)}\n"]
        for record in self.step_history:
            mark = "x" if record.verdict_status == "PASS" else " "
            label = record.step_id
            if record.attempt is not None:
                label += f" (attempt {record.attempt})"
            line = f"- [{mark}] {label}: {record.verdict_status} — {record.verdict_reason}"
            # Issue #222: 異常終了 attempt の終了コード / signal を可視化する。
            # clean exit（exit_code 0 / None かつ signal 無し）は detail を付けない
            # （設計書 § C の progress.md 例示は正常 PASS 行に exit 情報を出さない）。
            abnormal = (record.exit_code is not None and record.exit_code != 0) or (
                record.signal is not None
            )
            if abnormal:
                detail = f"exit {record.exit_code}" if record.exit_code is not None else "exit ?"
                if record.signal is not None:
                    detail += f", {record.signal}"
                line += f" ({detail})"
            lines.append(line)
        if self.cycle_counts:
            lines.append("\n## サイクル")
            for name, count in self.cycle_counts.items():
                lines.append(f"- {name}: {count} iterations")
        path = self._state_dir / "progress.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
