# Discord 웹훅 알림 모듈
import datetime
import logging
import httpx
from config import settings

logger = logging.getLogger(__name__)

# 모듈 수준 AsyncClient — 커넥션 풀 재사용 (매 호출마다 생성/파괴 방지)
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=5.0)
    return _client


# Discord 웹훅으로 메시지 전송 (URL 미설정 시 무시)
async def notify(msg: str) -> None:
    if not settings.discord_webhook_url:
        return
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        client = _http()
        await client.post(
            settings.discord_webhook_url,
            json={"content": f"[{now}] {msg}"},
        )
    except Exception as e:
        logger.error(f"Discord error: {e}")


# 앱 종료 시 클라이언트 정리
async def close() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
