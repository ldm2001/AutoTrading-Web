# 실시간 틱 큐 — producer/consumer 분리로 WebSocket 수신부 논블로킹 보장
import asyncio
import logging
import datetime
from collections.abc import Callable
from service.candle_store import store

logger = logging.getLogger(__name__)


class TickQueue:
    def __init__(self, maxsize: int = 10000) -> None:
        self._q: asyncio.Queue | None = None
        self._maxsize = maxsize
        self._task: asyncio.Task | None = None
        self._running = False
        # consumer 콜백
        self._handlers: list[Callable] = []

    # 이벤트 루프 내에서 Queue 생성
    def _ensure_queue(self) -> asyncio.Queue:
        if self._q is None:
            self._q = asyncio.Queue(maxsize=self._maxsize)
        return self._q

    # producer: WebSocket 수신부에서 호출 — put만 하고 즉시 반환
    async def push(self, code: str, price: int, volume: int,
                   ts: datetime.datetime | None = None) -> None:
        q = self._ensure_queue()
        tick = {"code": code, "price": price, "volume": volume, "ts": ts or datetime.datetime.now()}
        try:
            q.put_nowait(tick)
        except asyncio.QueueFull:
            # 큐 포화 시 가장 오래된 항목 버림
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            q.put_nowait(tick)

    # consumer 핸들러 등록
    def on_tick(self, handler: Callable) -> None:
        self._handlers.append(handler)

    # consumer 루프 시작
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._consume())
        logger.info("TickQueue consumer started")

    # consumer 루프 중지
    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TickQueue consumer stopped")

    # consumer: 큐에서 틱을 꺼내 캔들 조립 + 핸들러 실행
    async def _consume(self) -> None:
        q = self._ensure_queue()
        try:
            while self._running:
                tick = await asyncio.wait_for(q.get(), timeout=5.0)
                code = tick["code"]
                price = tick["price"]
                volume = tick["volume"]
                ts = tick["ts"]

                # 캔들 스토어에 적재 (15분/60분봉 자동 조립)
                await store.ingest(code, price, volume, ts)

                # 등록된 핸들러 실행 (SMC 분석, 동적 손절 등)
                for handler in self._handlers:
                    try:
                        result = handler(tick)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"Tick handler error: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"TickQueue consumer error: {e}")
        finally:
            self._running = False

    @property
    def qsize(self) -> int:
        return self._q.qsize() if self._q else 0

    @property
    def running(self) -> bool:
        return self._running


tick_q = TickQueue()
