# 거래 기록 — 메모리 로그 + 파일 저장 + on_trade 이벤트
import datetime
from collections.abc import Callable
from service.trading.records import trade_log

# records.trade_log 별칭 — 내부 호출과 테스트 몽키패치가 이 이름을 참조
trade_log_append = trade_log.append
trade_log_rows = trade_log.rows


# 거래 내역을 기록·보관하고 체결 이벤트를 발행
class TradeJournal:
    # 알림(notifier) + 포지션 영속 콜백(snap) 주입
    def __init__(self, notifier, snap: Callable[[], None]) -> None:
        self.notifier = notifier
        self.snap = snap
        self.logs: list[dict] = []
        self.ontrade: Callable | None = None

    # 오늘 거래 내역 복원
    def load(self) -> None:
        self.logs = trade_log_rows()

    # 거래 1건 기록 — 로그/파일/영속/알림/이벤트
    async def rec(self, code: str, name: str, kind: str, qty: int, price: int, ok: bool) -> None:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "time": now,
            "code": code,
            "name": name,
            "type": kind,
            "qty": qty,
            "price": price,
            "success": ok,
            "message": "성공" if ok else "실패",
        }
        self.logs.append(entry)
        trade_log_append(entry)
        self.snap()
        action = "매수" if kind == "buy" else "매도"
        await self.notifier.msg(f"[{action} {'성공' if ok else '실패'}] {name or code} {qty}주 @{price:,}")
        if self.ontrade:
            await self.ontrade(entry)
