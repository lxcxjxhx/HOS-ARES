"""B50 Playbook 资产：加载、校验、成功率一致性。

三个纪律（`31` / `H03 §2`）：

1. **schema 是硬门**：`plan-veripen/schemas/playbook.schema.json`，少字段即失败；
2. **oracle 必须真实存在**，且 `impact` 与 oracle 的分级一致（否则"打法"能指挥一个不存在的判定器）；
3. **成功率必须实测**：`success_rate: null` 表示未测；一旦填数，必须与 `playbooks/measured.json`
   里 ≥5 次演练的结果一致——**手改一个数就红**。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "plan-veripen" / "schemas" / "playbook.schema.json"
PLAYBOOK_DIR = ROOT / "playbooks"
MEASURED_PATH = PLAYBOOK_DIR / "measured.json"

#: 演练最少次数：低于这个数的"成功率"没有统计意义（D3）
MIN_SAMPLES = 5


class PlaybookError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Playbook:
    path: Path
    data: Mapping[str, Any]

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def oracle(self) -> str:
        return str(self.data["oracle"])

    @property
    def success_rate(self) -> float | None:
        value = self.data.get("success_rate")
        return None if value is None else float(value)


def _schema() -> dict[str, Any]:
    raw: Any = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return dict(raw)


def load_file(path: Path) -> Playbook:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PlaybookError("yaml_invalid", f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise PlaybookError("yaml_invalid", f"{path.name}: 顶层必须是映射")
    errors = sorted(Draft202012Validator(_schema()).iter_errors(data), key=lambda e: e.path)
    if errors:
        first = errors[0]
        raise PlaybookError("schema_invalid", f"{path.name}: {list(first.path)} {first.message}")
    return Playbook(path=path, data=data)


def load_dir(directory: Path | None = None) -> tuple[Playbook, ...]:
    base = directory or PLAYBOOK_DIR
    if not base.exists():
        raise PlaybookError("missing_dir", str(base))
    books = [load_file(p) for p in sorted(base.glob("*.yaml"))]
    if not books:
        raise PlaybookError("empty_dir", str(base))
    seen: set[str] = set()
    for book in books:
        if book.id in seen:
            raise PlaybookError("duplicate_id", book.id)
        seen.add(book.id)
    return tuple(books)


def validate_against_oracles(books: tuple[Playbook, ...],
                             table: Mapping[str, str]) -> None:
    """**表由调用方给**：核心包不依赖 oracle 包（契约测试守这条）。"""
    for book in books:
        if book.oracle not in table:
            raise PlaybookError("unknown_oracle", f"{book.id} → {book.oracle}")
        expected = table[book.oracle]
        if book.data["impact"] != expected:
            raise PlaybookError("impact_mismatch",
                                f"{book.id}: 声明 {book.data['impact']}，oracle 是 {expected}")


def load_measured(path: Path | None = None) -> dict[str, dict[str, Any]]:
    target = path or MEASURED_PATH
    if not target.exists():
        return {}
    raw: Any = json.loads(target.read_text(encoding="utf-8"))
    return {str(k): dict(v) for k, v in dict(raw).items()}


def check_success_rates(books: tuple[Playbook, ...],
                        measured: Mapping[str, Mapping[str, Any]] | None = None) -> None:
    """手填一个数就红：文件里的 `success_rate` 必须等于实测值，且样本数 ≥5。"""
    table = measured if measured is not None else load_measured()
    for book in books:
        claimed = book.success_rate
        record = table.get(book.id)
        if record is None:
            if claimed is not None:
                raise PlaybookError("unmeasured_rate",
                                    f"{book.id}: 写了 {claimed} 但没有实测记录（31 §1 禁止）")
            continue
        samples = int(record.get("samples", 0))
        if samples < MIN_SAMPLES:
            raise PlaybookError("insufficient_samples", f"{book.id}: {samples} 次 < {MIN_SAMPLES}")
        actual = float(record["success_rate"])
        if claimed is None:
            raise PlaybookError("missing_rate", f"{book.id}: 已实测 {actual}，文件里还是 null")
        if abs(float(claimed) - actual) > 1e-9:
            raise PlaybookError("rate_mismatch", f"{book.id}: 文件 {claimed} ≠ 实测 {actual}")


def load_all(directory: Path | None = None,
             measured: Mapping[str, Mapping[str, Any]] | None = None,
             oracle_table: Mapping[str, str] | None = None) -> tuple[Playbook, ...]:
    """一次跑完全部检查；任何一条不过就抛 `PlaybookError`。

    ``oracle_table`` 由调用方注入（CLI 从 oracle 包构造），
    这样核心包既不依赖 oracle 包，也能拿到"oracle 是否真实存在"的校验。
    """
    books = load_dir(directory)
    if oracle_table is not None:
        validate_against_oracles(books, oracle_table)
    check_success_rates(books, measured)
    return books


__all__ = [
    "MIN_SAMPLES",
    "MEASURED_PATH",
    "PLAYBOOK_DIR",
    "Playbook",
    "PlaybookError",
    "check_success_rates",
    "load_all",
    "load_dir",
    "load_measured",
    "validate_against_oracles",
]
