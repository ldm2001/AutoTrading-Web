# Discord 웹훅 알림 — 비동기 큐 + 단일 소비 태스크 (주문 경로 비차단)
import asyncio
import datetime
import logging
import httpx
from config import settings

logger = logging.getLogger(__name__)

# 큐 용량 — 초과 시 신규 메시지 드롭 (백프레셔)
_MAXSIZE = 200

# 모듈 수준 AsyncClient — 커넥션 풀 재사용 (매 호출마다 생성/파괴 방지)
_client: httpx.AsyncClient | None = None
_queue: asyncio.Queue[str] | None = None
_task: asyncio.Task | None = None
_dropped: int = 0


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=5.0)
    return _client


# 429 응답의 대기 시간 (바디 retry_after 우선, 없으면 헤더, 기본 1초)
def _wait(resp: httpx.Response) -> float:
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("retry_after") is not None:
            return float(body["retry_after"])
    except Exception:
        pass
    header = resp.headers.get("retry-after")
    try:
        return float(header) if header else 1.0
    except (TypeError, ValueError):
        return 1.0


# 실제 웹훅 POST — 429 시 retry_after 만큼 1회 대기 후 재시도
async def _post(msg: str) -> None:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {"content": f"[{now}] {msg}"}
    client = _http()
    resp = await client.post(settings.discord_webhook_url, json=payload)
    if resp.status_code == 429:
        await asyncio.sleep(_wait(resp))
        await client.post(settings.discord_webhook_url, json=payload)


# 소비 태스크 — 큐에서 꺼내 순차 전송 (FIFO, 단일 소비자)
async def _consume() -> None:
    while True:
        msg = await _queue.get()
        try:
            await _post(msg)
        except Exception as e:
            logger.error("Discord send failed: %s", e)
        finally:
            _queue.task_done()


# 큐 + 소비 태스크 기동 (멱등) — webhook 미설정 시 no-op
async def start() -> None:
    global _queue, _task, _dropped
    if not settings.discord_webhook_url:
        return
    if _task is not None and not _task.done():
        return
    _queue = asyncio.Queue(maxsize=_MAXSIZE)
    _dropped = 0
    _task = asyncio.create_task(_consume())


# 메시지 enqueue — put_nowait만 수행해 즉시 반환 (큐 가득 시 드롭+카운트)
async def notify(msg: str) -> None:
    global _dropped
    if not settings.discord_webhook_url or _queue is None:
        return
    try:
        _queue.put_nowait(msg)
    except asyncio.QueueFull:
        _dropped += 1
        logger.warning("Discord queue full — message dropped (total=%s)", _dropped)


# 종료 — 5초 한도 drain → 소비 태스크 취소 → 클라이언트 정리
async def close() -> None:
    global _client, _queue, _task
    if _queue is not None:
        try:
            await asyncio.wait_for(_queue.join(), timeout=5.0)
        except TimeoutError:
            logger.warning("Discord drain timeout — %s pending", _queue.qsize())
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    _queue = None
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
