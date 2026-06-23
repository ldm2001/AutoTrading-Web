# 알림 단일 책임 — 로그 + Discord 큐 enqueue + WS 콜백 라우팅
import datetime
import logging
from collections.abc import Callable
from service.discord import notify

logger = logging.getLogger(__name__)

# 봇 메시지 라우팅 (로그 / Discord / WS onmessage)
class Notifier:
    def __init__(self) -> None:
        self.onmessage: Callable | None = None

    async def msg(self, text: str) -> None:
        logger.info(text)
        await notify(text)
        if self.onmessage:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await self.onmessage(f"[{now}] {text}")
