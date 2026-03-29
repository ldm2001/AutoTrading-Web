# 폴링 시세 -> pseudo-tick 정규화
import datetime
import logging

from service.market.candle_store import CandleStore, store
from service.market.tick_queue import TickQueue, tick_q

logger = logging.getLogger(__name__)

# 폴링 시세를 pseudo-tick으로 변환하여 큐에 전달
class PriceSync:
    # 틱 큐 및 캔들 스토어 의존성 주입
    def __init__(self, queue: TickQueue, candles: CandleStore) -> None:
        self.queue = queue
        self.candles = candles
        self._last_volume: dict[str, int] = {}
        self._volume_day: str | None = None
        self._last_flushed_day: str | None = None

    # 날짜 변경 시 누적 거래량 초기화
    def _ensure_day(self, date_str: str) -> None:
        if self._volume_day != date_str:
            self._volume_day = date_str
            self._last_volume.clear()

    # 단일 틱을 큐에 전달
    async def tick(
        self,
        code: str,
        price: int,
        volume: int,
        ts: datetime.datetime | None = None,
    ) -> None:
        await self.queue.push(code, price, volume, ts)

    # 폴링 시세 → 거래량 차분 계산 후 pseudo-tick 생성
    async def snap(
        self,
        stocks: list[dict],
        ts: datetime.datetime | None = None,
    ) -> None:
        ts = ts or datetime.datetime.now()
        date_str = ts.date().isoformat()
        self._ensure_day(date_str)

        for stock in stocks:
            code = str(stock.get("code", "")).zfill(6)
            price = int(stock.get("price", 0) or 0)
            if not code or price <= 0:
                continue

            current_volume = int(stock.get("volume", 0) or 0)
            previous_volume = self._last_volume.get(code)
            volume_delta = 0
            if previous_volume is not None and current_volume >= previous_volume:
                volume_delta = current_volume - previous_volume
            self._last_volume[code] = current_volume

            await self.tick(code, price, volume_delta, ts)

    # 장 마감 후 캔들 데이터 파일로 저장
    async def flush_day(self, date_str: str | None = None) -> int:
        date_str = date_str or datetime.date.today().isoformat()
        if self._last_flushed_day == date_str:
            return 0
        saved = await self.candles.flush(date_str)
        self._last_flushed_day = date_str
        if saved:
            logger.info("Flushed candle store for %s (%s files)", date_str, saved)
        return saved


price_sync = PriceSync(tick_q, store)
