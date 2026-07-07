# 주문/체결 기록 저장소 — 날짜별 JSONL 파일 + ES 인덱싱 (구 trade_log/order_log 통합)
import datetime
import json
from pathlib import Path
from service.infra.elk import order as elk_order

_BASE = Path(__file__).resolve().parent.parent / "trades"


# 날짜별 JSONL 파일에 기록을 누적하는 저장소
class JsonlLog:
    # kind별 저장 디렉토리 준비
    def __init__(self, kind: str) -> None:
        self._dir = _BASE / kind
        self._dir.mkdir(parents=True, exist_ok=True)

    # 날짜별 로그 파일 경로
    def path(self, date: str | None = None) -> Path:
        if date is None:
            date = datetime.date.today().isoformat()
        return self._dir / f"{date}.jsonl"

    # 로그 한 줄 추가 (+ES 인덱싱)
    def append(self, entry: dict) -> None:
        f = self.path()
        line = json.dumps(entry, ensure_ascii=False)
        with f.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")
        elk_order(entry)

    # 날짜별 로그 조회
    def rows(self, date: str | None = None) -> list[dict]:
        f = self.path(date)
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


# 주문 감사 로그 (KIS 주문 API 호출 전수 기록)
order_log = JsonlLog("orders")

# 봇 체결 기록 (TradeJournal 영속 저장소)
trade_log = JsonlLog("executions")
