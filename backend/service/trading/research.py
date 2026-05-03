# Walk-forward 검증 기록 보관
import datetime
import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2] / "data" / "research"


def wftab(code: str, data: dict[str, Any], root: Path | None = None) -> int:
    base = root or _ROOT
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{code}.jsonl"
    row = {
        "code": code,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        **data,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(wfrows(code, root=base))


def wfrows(code: str, root: Path | None = None) -> list[dict[str, Any]]:
    base = root or _ROOT
    path = base / f"{code}.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows
