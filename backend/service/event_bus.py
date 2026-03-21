# Redis Pub/Sub 기반 이벤트 버스 — 폴링 제거를 위한 내부 이벤트 전달
import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_CHANNEL = "events"

class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}
        self._redis = None
        self._task: asyncio.Task | None = None
        # Redis 없을 때 인프로세스 폴백
        self._local_queue: asyncio.Queue | None = None

    # Redis 연결 설정
    def bind(self, redis_client) -> None:
        self._redis = redis_client

    # 이벤트 핸들러 등록
    def on(self, event: str, handler: Callable) -> None:
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)

    # 이벤트 발행
    async def emit(self, event: str, data: Any = None) -> None:
        payload = json.dumps({"event": event, "data": data}, default=str)

        if self._redis is not None:
            try:
                self._redis.publish(_CHANNEL, payload)
                return
            except Exception:
                pass

        # 인프로세스 폴백
        if self._local_queue is None:
            self._local_queue = asyncio.Queue(maxsize=1000)
        try:
            self._local_queue.put_nowait({"event": event, "data": data})
        except asyncio.QueueFull:
            pass

    # 구독 루프 시작
    async def start(self) -> None:
        if self._redis is not None:
            self._task = asyncio.create_task(self._redis_loop())
        else:
            self._task = asyncio.create_task(self._local_loop())
        logger.info("EventBus started (redis=%s)", self._redis is not None)

    # 구독 루프 중지
    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # Redis Pub/Sub 구독 루프
    async def _redis_loop(self) -> None:
        try:
            pubsub = self._redis.pubsub()
            pubsub.subscribe(_CHANNEL)
            while True:
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    try:
                        payload = json.loads(msg["data"])
                        await self._dispatch(payload["event"], payload.get("data"))
                    except Exception as e:
                        logger.debug("EventBus dispatch error: %s", e)
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("EventBus redis loop error: %s", e)

    # 인프로세스 폴백 루프
    async def _local_loop(self) -> None:
        if self._local_queue is None:
            self._local_queue = asyncio.Queue(maxsize=1000)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(self._local_queue.get(), timeout=1.0)
                    await self._dispatch(payload["event"], payload.get("data"))
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass

    # 핸들러 디스패치
    async def _dispatch(self, event: str, data: Any) -> None:
        handlers = self._handlers.get(event, [])
        for handler in handlers:
            try:
                result = handler(event, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("EventBus handler error (%s): %s", event, e)

bus = EventBus()
