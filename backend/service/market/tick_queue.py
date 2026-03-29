# 실시간 틱 큐 — Kafka 기반 producer/consumer (폴백: asyncio.Queue)
import asyncio
import datetime
import json
import logging
from collections.abc import Callable
from config import settings
from service.market.candle_store import store
from service.metrics import tick_queue_size, tick_queue_drops, candle_ingest
from service.event_bus import bus

logger = logging.getLogger(__name__)

# Kafka 기반 틱 큐 (폴백: asyncio.Queue)
class TickQueue:
    # 큐 초기화 — Kafka 및 asyncio.Queue 대기
    def __init__(self, maxsize: int = 10000) -> None:
        self._maxsize = maxsize
        self._running = False
        self._task: asyncio.Task | None = None
        self._handlers: list[Callable] = []
        # asyncio.Queue 폴백
        self._q: asyncio.Queue | None = None
        # Kafka
        self._producer = None
        self._consumer = None
        self._use_kafka = False

    # asyncio.Queue 인스턴스 생성 (렊은 초기화)
    def _ensure_queue(self) -> asyncio.Queue:
        if self._q is None:
            self._q = asyncio.Queue(maxsize=self._maxsize)
        return self._q

    # Kafka 연결 시도
    async def _init_kafka(self) -> bool:
        try:
            from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
            self._producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v, default=str).encode(),
                key_serializer=lambda k: k.encode() if k else None,
            )
            await self._producer.start()

            self._consumer = AIOKafkaConsumer(
                settings.kafka_tick_topic,
                bootstrap_servers=settings.kafka_bootstrap,
                group_id="candle-builder",
                value_deserializer=lambda v: json.loads(v.decode()),
                auto_offset_reset="latest",
                enable_auto_commit=True,
            )
            await self._consumer.start()
            self._use_kafka = True
            logger.info("Kafka connected: %s topic=%s", settings.kafka_bootstrap, settings.kafka_tick_topic)
            return True
        except Exception as e:
            logger.warning("Kafka unavailable, using asyncio.Queue fallback: %s", e)
            self._producer = None
            self._consumer = None
            self._use_kafka = False
            return False

    # producer: WebSocket 수신부에서 호출 — put만 하고 즉시 반환
    async def push(self, code: str, price: int, volume: int,
                   ts: datetime.datetime | None = None) -> None:
        tick = {
            "code": code,
            "price": price,
            "volume": volume,
            "ts": (ts or datetime.datetime.now()).isoformat(),
        }

        if self._use_kafka and self._producer is not None:
            try:
                await self._producer.send(
                    settings.kafka_tick_topic,
                    value=tick,
                    key=code,
                )
                return
            except Exception as e:
                logger.warning("Kafka produce failed, fallback to queue: %s", e)

        # asyncio.Queue 폴백
        q = self._ensure_queue()
        try:
            q.put_nowait(tick)
        except asyncio.QueueFull:
            tick_queue_drops.inc()
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
        await self._init_kafka()
        if self._use_kafka:
            self._task = asyncio.create_task(self._consume_kafka())
        else:
            self._task = asyncio.create_task(self._consume_queue())
        logger.info("TickQueue consumer started (kafka=%s)", self._use_kafka)

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
        if self._producer:
            try:
                await self._producer.stop()
            except Exception:
                pass
            self._producer = None
        if self._consumer:
            try:
                await self._consumer.stop()
            except Exception:
                pass
            self._consumer = None
        logger.info("TickQueue consumer stopped")

    # 틱 처리 공통 로직
    async def _process(self, tick: dict) -> None:
        code = tick["code"]
        price = tick["price"]
        volume = tick["volume"]
        ts_raw = tick.get("ts")
        if isinstance(ts_raw, str):
            try:
                ts = datetime.datetime.fromisoformat(ts_raw)
            except ValueError:
                ts = datetime.datetime.now()
        elif isinstance(ts_raw, datetime.datetime):
            ts = ts_raw
        else:
            ts = datetime.datetime.now()

        await store.ingest(code, price, volume, ts)
        candle_ingest.labels(interval="15m").inc()
        candle_ingest.labels(interval="60m").inc()

        for handler in self._handlers:
            try:
                result = handler(tick)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("Tick handler error: %s", e)

        await bus.emit("tick", {"code": code, "price": price, "volume": volume})

    # Kafka consumer 루프
    async def _consume_kafka(self) -> None:
        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                await self._process(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Kafka consumer error: %s", e)
        finally:
            self._running = False

    # asyncio.Queue consumer 루프 (폴백)
    async def _consume_queue(self) -> None:
        q = self._ensure_queue()
        try:
            while self._running:
                try:
                    tick = await asyncio.wait_for(q.get(), timeout=5.0)
                except TimeoutError:
                    continue
                tick_queue_size.set(q.qsize())
                await self._process(tick)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("TickQueue consumer error: %s", e)
        finally:
            self._running = False

    # 현재 큐 크기 반환
    @property
    def qsize(self) -> int:
        if self._q:
            return self._q.qsize()
        return 0

    # 실행 상태 반환
    @property
    def running(self) -> bool:
        return self._running

    # Kafka 연결 상태 반환
    @property
    def kafka_enabled(self) -> bool:
        return self._use_kafka


tick_q = TickQueue()
