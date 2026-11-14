"""agent step の verdict を検証・直列化して原子的に公開する（Issue #568）。

agent が ``verdict.yaml`` を手書きすると、字下げ 1 文字の誤りで副作用完了後に判定だけが
失われる。判定内容（status / reason / evidence / suggestion）は agent が決め、本 module が
runner 由来の契約に照らして形式を検証し、既存形式の pure YAML として 1 回だけ公開する。

runner は agent 起動前に attempt dir へ契約ファイル ``verdict-contract.json`` を書き、
その絶対パスを環境変数 ``KAJI_VERDICT_CONTRACT`` で agent プロセスへ渡す。公開先パスと
許容 status は契約からのみ決まり、agent の自己申告では決まらない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import (
    InvalidVerdictValue,
    VerdictParseError,
    VerdictPublishContextError,
    VerdictPublishInputError,
)
from .fsio import atomic_publish_new, atomic_write
from .result import RESULT_FILE
from .verdict import dump_verdict_yaml, normalize_verdict_fields, validate_verdict_fields

VERDICT_CONTRACT_ENV = "KAJI_VERDICT_CONTRACT"
VERDICT_CONTRACT_FILENAME = "verdict-contract.json"
VERDICT_FILENAME = "verdict.yaml"


class VerdictContract(BaseModel):
    """runner が attempt ごとに書く verdict 公開契約。

    ``verdict.yaml`` の schema は変えず（ADR 012）、attempt の識別情報は本ファイルに持つ。

    Attributes:
        schema_version: 契約 schema の版。現行は 1 のみ。
        issue_id: 正規化済み Issue ID。
        run_id: ``runs/<run_id>/`` のディレクトリ名。
        step_id: 現在の step ID。
        attempt: 1 始まりの attempt 番号。
        verdict_path: 公開先 ``verdict.yaml`` の絶対パス。親は契約ファイルと同じディレクトリ。
        valid_statuses: 現在の step の ``on:`` キー（定義順）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    issue_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    verdict_path: str
    valid_statuses: list[str] = Field(min_length=1)

    @field_validator("verdict_path")
    @classmethod
    def _check_verdict_path(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("verdict_path must be absolute")
        if path.name != VERDICT_FILENAME:
            raise ValueError(f"verdict_path must be named {VERDICT_FILENAME}")
        return value


@dataclass(frozen=True)
class PublishedVerdict:
    """公開に成功した verdict の要約。

    Attributes:
        path: 公開した ``verdict.yaml``。
        step_id: 契約の step ID。
        attempt: 契約の attempt 番号。
        status: 公開した status。
        cleanup_warning: 公開確定後に一時ファイルを削除できなかった場合の警告文。
    """

    path: Path
    step_id: str
    attempt: int
    status: str
    cleanup_warning: str | None = None


def write_verdict_contract(
    attempt_dir: Path,
    *,
    issue_id: str,
    run_id: str,
    step_id: str,
    attempt: int,
    valid_statuses: Sequence[str],
) -> Path:
    """attempt dir に verdict 公開契約を原子的に書き、そのパスを返す。

    Args:
        attempt_dir: 当該 attempt のディレクトリ（作成済み）。
        issue_id: 正規化済み Issue ID。
        run_id: ``runs/<run_id>/`` のディレクトリ名。
        step_id: 現在の step ID。
        attempt: 1 始まりの attempt 番号。
        valid_statuses: 現在の step の ``on:`` キー（定義順）。

    Returns:
        契約ファイルの絶対パス。
    """
    base = attempt_dir.absolute()
    contract = VerdictContract(
        schema_version=1,
        issue_id=issue_id,
        run_id=run_id,
        step_id=step_id,
        attempt=attempt,
        verdict_path=str(base / VERDICT_FILENAME),
        valid_statuses=list(valid_statuses),
    )
    contract_path = base / VERDICT_CONTRACT_FILENAME
    atomic_write(contract_path, contract.model_dump_json(indent=2) + "\n")
    return contract_path


def load_verdict_contract(environ: Mapping[str, str]) -> tuple[Path, VerdictContract]:
    """``KAJI_VERDICT_CONTRACT`` が指す契約を読み、検証する。

    Args:
        environ: 環境変数（通常は ``os.environ``）。

    Returns:
        ``(契約ファイルのパス, 検証済み契約)``。

    Raises:
        VerdictPublishContextError: 変数が未設定・相対パス、契約ファイルの欠落・破損、
            ``verdict_path`` が契約ファイルと別ディレクトリを指す。
    """
    raw = environ.get(VERDICT_CONTRACT_ENV, "")
    if not raw:
        raise VerdictPublishContextError(
            f"{VERDICT_CONTRACT_ENV} is not set; kaji verdict publish only works inside "
            "a kaji agent step"
        )
    contract_path = Path(raw)
    if not contract_path.is_absolute():
        raise VerdictPublishContextError(f"{VERDICT_CONTRACT_ENV} must be an absolute path: {raw}")
    try:
        text = contract_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise VerdictPublishContextError(f"verdict contract not found: {contract_path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise VerdictPublishContextError(
            f"cannot read verdict contract {contract_path}: {exc}"
        ) from exc
    try:
        contract = VerdictContract.model_validate_json(text)
    except ValidationError as exc:
        raise VerdictPublishContextError(
            f"invalid verdict contract {contract_path}: {exc}"
        ) from exc
    if Path(contract.verdict_path).parent.resolve() != contract_path.parent.resolve():
        raise VerdictPublishContextError(
            f"verdict_path must be in the same directory as the contract {contract_path}: "
            f"{contract.verdict_path}"
        )
    return contract_path, contract


def _already_published_message(verdict_path: Path) -> str:
    return (
        f"verdict is already published for this attempt: {verdict_path} "
        "(check the existing artifact; do not write it by hand)"
    )


def publish_agent_verdict(
    environ: Mapping[str, str],
    *,
    status: str,
    reason: str,
    evidence: str,
    suggestion: str,
) -> PublishedVerdict:
    """現在の attempt の ``verdict.yaml`` を検証・直列化して 1 回だけ公開する。

    手順: 状況検証 → 正規化（strip + 制御文字置換）→ 入力検証 → 直列化（round-trip 保証）
    → ``fsio.atomic_publish_new``。判定内容の真偽は推測しない（形式だけを検証する）。

    Args:
        environ: 環境変数（通常は ``os.environ``）。``KAJI_VERDICT_CONTRACT`` を読む。
        status: 判定 status。契約の ``valid_statuses`` と完全一致が必要。
        reason: 判定理由。前後の空白を除いて非空が必要。
        evidence: 具体的根拠。前後の空白を除いて非空が必要。
        suggestion: 次のアクション提案。ABORT / BACK* では非空が必要。

    Returns:
        公開結果。

    Raises:
        VerdictPublishContextError: 契約の不備、終了済み attempt、公開済み ``verdict.yaml``。
        VerdictPublishInputError: 入力が公開規則を満たさない（直して再実行できる）。
        VerdictSerializationError: 直列化が同じ値に読み戻せない（内部不変条件違反）。
        OSError: link 前の I/O に失敗した（``verdict.yaml`` は作られていない）。
    """
    contract_path, contract = load_verdict_contract(environ)
    verdict_path = Path(contract.verdict_path)
    if (contract_path.parent / RESULT_FILE).exists():
        raise VerdictPublishContextError(
            f"attempt already finished ({RESULT_FILE} exists): {contract_path.parent}"
        )
    if verdict_path.exists():
        raise VerdictPublishContextError(_already_published_message(verdict_path))

    verdict = normalize_verdict_fields(
        status=status, reason=reason, evidence=evidence, suggestion=suggestion
    )
    try:
        validate_verdict_fields(verdict, contract.valid_statuses)
    except (InvalidVerdictValue, VerdictParseError) as exc:
        allowed = ", ".join(contract.valid_statuses)
        raise VerdictPublishInputError(f"{exc} (allowed statuses: {allowed})") from exc

    text = dump_verdict_yaml(verdict)
    try:
        outcome = atomic_publish_new(verdict_path, text)
    except FileExistsError:
        raise VerdictPublishContextError(_already_published_message(verdict_path)) from None

    cleanup_warning: str | None = None
    if outcome.cleanup_error is not None:
        cleanup_warning = (
            f"verdict is published, but the temporary file could not be removed: "
            f"{outcome.cleanup_error}"
        )
    return PublishedVerdict(
        path=verdict_path,
        step_id=contract.step_id,
        attempt=contract.attempt,
        status=verdict.status,
        cleanup_warning=cleanup_warning,
    )
