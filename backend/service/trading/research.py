# Walk-forward 검증 기록 보관 — 매수 진입/청산 이벤트를 종목별 jsonl로 누적
import datetime
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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

# 매수 진입 로그 — 9팩터 평가 결과 + 체결 정보 적재
# 실거래 결과(wfout)와 후처리에서 매칭하여 가중치 검증에 사용
def wfin(
    code: str, evaluation: dict[str, Any], qty: int, root: Path | None = None,
) -> None:
    try:
        factors = [
            {"name": f.get("name"), "score": f.get("score")}
            for f in evaluation.get("factors", [])
        ]
        wftab(
            code,
            {
                "kind": "entry",
                "score": evaluation.get("score"),
                "signal": evaluation.get("signal"),
                "price": evaluation.get("price"),
                "stop_price": evaluation.get("stop_price"),
                "qty": qty,
                "factors": factors,
            },
            root=root,
        )
    except Exception as e:
        logger.warning("wfin %s logging failed: %s", code, type(e).__name__)


# 매도 청산 로그 — reason: stop / profit / eod / manual
def wfout(
    code: str,
    reason: str,
    exit_price: int,
    qty: int,
    pnl_pct: float | None = None,
    root: Path | None = None,
) -> None:
    try:
        wftab(
            code,
            {
                "kind": "exit",
                "reason": reason,
                "price": exit_price,
                "qty": qty,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            },
            root=root,
        )
    except Exception as e:
        logger.warning("wfout %s logging failed: %s", code, type(e).__name__)
