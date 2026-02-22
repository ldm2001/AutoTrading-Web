# Discord 웹훅 알림 모듈
import datetime
import logging
import httpx
from config import settings

logger = logging.getLogger(__name__)

# Discord 웹훅으로 메시지 전송 (URL 미설정 시 무시)
async def notify(msg: str) -> None:
    if not settings.discord_webhook_url:
        return
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                settings.discord_webhook_url,
                json={"content": f"[{now}] {msg}"},
                timeout=5.0,
            )
    except Exception as e:
        logger.error(f"Discord error: {e}")
