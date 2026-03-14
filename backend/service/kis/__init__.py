from service.kis.auth import Auth
from service.kis.market import Market
from service.kis.trade import Trade
from service.kis.ws import KISWS
from service.trading.order_log import append as order_log_append
from service.policy import Policy
from service.market.price_sync import price_sync
from service.market.stock_universe import ALL_STOCKS, CODES, INDICES, NAMES, listing, search
from service.ttl_cache import TTLCache

# 분리된 KIS 기능을 묶는 facade
class KIS:
    TTL_PRICE = Market.TTL_PRICE
    TTL_OB = Market.TTL_OB
    TTL_DAILY = Market.TTL_DAILY
    TTL_15M = Market.TTL_15M
    TTL_INDEX = Market.TTL_INDEX
    TTL_HOLDINGS = Trade.TTL_HOLDINGS
    TTL_CASH = Trade.TTL_CASH
    TTL_TARGET = Market.TTL_TARGET

    def __init__(self) -> None:
        self.cache = TTLCache()
        self.policy = Policy()
        self.auth = Auth(self.policy)
        self.market = Market(self.auth, self.cache, self.policy)
        self.trade = Trade(self.auth, self.cache, self.policy, audit=order_log_append)
        self.ws = KISWS(self.auth, price_sync)

    # KIS 세션 오픈
    async def start(self) -> None:
        await self.auth.open()

    # KIS 세션을 닫음
    async def stop(self) -> None:
        await self.auth.close()

    # 현재가 요약을 조회
    async def price(self, code: str) -> dict:
        return await self.market.price(code)

    # 현재가 숫자만 반환
    async def price_raw(self, code: str) -> int:
        return await self.market.price_raw(code)

    # 5호가를 조회
    async def orderbook(self, code: str) -> dict:
        return await self.market.orderbook(code)

    # 일봉 캔들을 조회
    async def daily(self, code: str, count: int = 60) -> list[dict]:
        return await self.market.daily(code, count)

    # 15분봉 캔들을 조회
    async def candles_15m(self, code: str) -> list[dict]:
        return await self.market.candles_15m(code)

    # 종목 목록 현재가를 모아 조회
    async def prices(self, codes: list[str] | None = None) -> list[dict]:
        return await self.market.prices(codes)

    # 변동성 돌파 목표가를 계산
    async def target(self, code: str) -> float:
        return await self.market.target(code)

    # 단일 지수 시세를 조회
    async def index(self, code: str) -> dict:
        return await self.market.index(code)

    # 주요 지수 시세를 조회
    async def indices(self) -> list[dict]:
        return await self.market.indices()

    # 보유 종목과 요약을 조회
    async def holdings(self) -> tuple[dict[str, dict], dict]:
        return await self.trade.holdings()

    # 주문 가능 현금을 조회
    async def cash(self) -> int:
        return await self.trade.cash()

    # 시장가 매수를 요청
    async def buy(self, code: str, qty: int) -> dict:
        return await self.trade.buy(code, qty)

    # 시장가 매도를 요청
    async def sell(self, code: str, qty: int) -> dict:
        return await self.trade.sell(code, qty)

    # 실시간 체결 구독을 맞춤
    async def ws_sync(self, codes: list[str]) -> None:
        await self.ws.sync(codes)

    # 실시간 체결 연결을 닫음
    async def ws_close(self) -> None:
        await self.ws.close()

    # 실시간 최신 시세를 seed 한다
    def ws_seed(self, items: list[dict]) -> None:
        self.ws.seed(items)

    # 실시간 최신 시세 목록을 반환
    def ws_rows(self, codes: list[str] | None = None) -> list[dict]:
        return self.ws.rows(codes)

    # 실시간 수신 상태를 반환
    def ws_live(self) -> bool:
        return self.ws.live()

kis = KIS()
