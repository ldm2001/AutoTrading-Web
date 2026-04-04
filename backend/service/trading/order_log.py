# 주문 감사 로그 저장소
import datetime
import json
from pathlib import Path
from service.elk import order as elk_order

_DIR = Path(__file__).resolve().parent.parent / "trades" / "orders"
_DIR.mkdir(parents=True, exist_ok=True)

# 날짜별 주문 로그 파일 경로
def path(date: str | None = None) -> Path:
    if date is None:
        date = datetime.date.today().isoformat()
    return _DIR / f"{date}.jsonl"

# 주문 로그 한 줄 추가
def append(entry: dict) -> None:
    f = path()
    line = json.dumps(entry, ensure_ascii=False)
    with f.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    elk_order(entry)

# 날짜별 주문 로그 조회
def rows(date: str | None = None) -> list[dict]:
    f = path(date)
    if not f.exists():
        return []

    out: list[dict] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
