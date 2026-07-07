# 보유 포지션 상태 — Redis 영속 + 실제 잔고 대조
import json
import logging

logger = logging.getLogger(__name__)

_REDIS_BOT_KEY = "bot:state"

# 보유/대기 포지션 상태를 소유하고 영속·대조
class PositionBook:
    # broker(Account: holdings, cache) + 종목명 맵 주입
    def __init__(self, broker, names: dict[str, str]) -> None:
        self.broker = broker
        self.names = names
        self.bought: dict[str, dict] = {} # {"avg_price": int, "qty": int, "name": str}
        self.pending_buys: set[str] = set()
        self.pending_stops: dict[str, float] = {}

    # 봇 시작 시 상태 초기화
    def reset(self) -> None:
        self.bought = {}
        self.pending_buys = set()
        self.pending_stops = {}

    # Redis에 보유 상태 저장
    def snap(self) -> None:
        r = self.broker.cache.redis
        if r is None:
            return
        try:
            r.set(_REDIS_BOT_KEY, json.dumps(self.bought, default=str))
        except Exception as e:
            logger.warning("Bot state save failed: %s", e)

    # Redis에서 보유 상태 복원
    def redo(self) -> None:
        r = self.broker.cache.redis
        if r is None:
            return
        try:
            raw = r.get(_REDIS_BOT_KEY)
            if raw:
                self.bought = json.loads(raw)
                logger.info("Bot state restored from Redis: %s positions", len(self.bought))
        except Exception as e:
            logger.warning("Bot state restore failed: %s", e)

    # 보유 항목 dict 구성 (손절가 승계 포함)
    def acct(self, code: str, info: dict, stop_price: float | None = None) -> dict:
        pos = {
            "avg_price": int(info.get("avg_price", 0)),
            "qty": int(info.get("qty", 0)),
            "name": info.get("name") or self.names.get(code, code),
        }
        if stop_price is not None:
            pos["stop_price"] = stop_price
        elif code in self.bought and self.bought[code].get("stop_price") is not None:
            pos["stop_price"] = self.bought[code]["stop_price"]
        return pos

    # 보유 종목 제거 + 영속
    def drop(self, code: str) -> None:
        if code in self.bought:
            del self.bought[code]
            self.snap()

    # 실제 잔고로 단일 종목 반영
    async def pos(self, code: str, stop_price: float | None = None) -> dict | None:
        items, _ = await self.broker.holdings()
        info = items.get(code)
        if not info:
            return None
        self.bought[code] = self.acct(code, info, stop_price)
        self.pending_buys.discard(code)
        self.pending_stops.pop(code, None)
        self.snap()
        return self.bought[code]

    # 대기 매수를 실제 잔고와 재대조
    async def pend(self) -> None:
        if not self.pending_buys:
            return
        items, _ = await self.broker.holdings()
        for code in list(self.pending_buys):
            info = items.get(code)
            if info:
                self.bought[code] = self.acct(code, info, self.pending_stops.get(code))
                self.pending_buys.discard(code)
                self.pending_stops.pop(code, None)
        self.snap()

    # 계좌 보유 중 봇 미추적 종목 (상태 복원 유실·수동 보유 감지)
    async def untracked(self) -> dict[str, dict]:
        items, _ = await self.broker.holdings()
        return {c: i for c, i in items.items() if c not in self.bought}
