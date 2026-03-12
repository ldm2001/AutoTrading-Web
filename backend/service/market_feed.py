# 폴링 시세 -> pseudo-tick 정규화 브리지
import datetime
import logging

from service.candle_store import store
from service.tick_queue import tick_q

logger = logging.getLogger(__name__)

class MarketFeed:
    def __init__(self) -> None:
        self._last_volume: dict[str, int] = {}
        self._volume_day: str | None = None
        self._last_flushed_day: str | None = None

    def _ensure_day(self, date_str: str) -> None:
        if self._volume_day != date_str:
            self._volume_day = date_str
            self._last_volume.clear()

    async def ingest_snapshot(
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

            await tick_q.push(code, price, volume_delta, ts)

    async def flush_day(self, date_str: str | None = None) -> int:
        date_str = date_str or datetime.date.today().isoformat()
        if self._last_flushed_day == date_str:
            return 0
        saved = await store.flush(date_str)
        self._last_flushed_day = date_str
        if saved:
            logger.info("Flushed candle store for %s (%s files)", date_str, saved)
        return saved


market_feed = MarketFeed()
