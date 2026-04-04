# API Key 인증 의존성 + brute-force lockout
import time
import secrets
import logging
from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from config import settings

logger = logging.getLogger(__name__)

_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# IP별 실패 추적 (LRU 방식 — 최대 1024개 IP만 보관)
_failures: dict[str, list[float]] = {}
_LOCKOUT_THRESHOLD = 5
_LOCKOUT_WINDOW = 900  # 15분
_MAX_TRACKED_IPS = 1024


# 오래된 IP 엔트리 정리 (최대 크기 초과 시 가장 오래된 것부터 제거)
def _evict() -> None:
    if len(_failures) <= _MAX_TRACKED_IPS:
        return
    now = time.time()
    # 먼저 만료된 엔트리 제거
    expired = [ip for ip, ts in _failures.items() if not ts or now - ts[-1] >= _LOCKOUT_WINDOW]
    for ip in expired:
        del _failures[ip]
    # 여전히 초과면 가장 오래된 엔트리 제거
    while len(_failures) > _MAX_TRACKED_IPS:
        oldest_ip = min(_failures, key=lambda ip: _failures[ip][-1] if _failures[ip] else 0)
        del _failures[oldest_ip]


# IP 잠금 여부 확인 (15분 내 5회 실패 시 차단)
def _check_lockout(ip: str) -> None:
    now = time.time()
    if ip in _failures:
        _failures[ip] = [t for t in _failures[ip] if now - t < _LOCKOUT_WINDOW]
        if not _failures[ip]:
            del _failures[ip]
            return
    else:
        return
    if len(_failures[ip]) >= _LOCKOUT_THRESHOLD:
        logger.warning("IP locked out due to repeated auth failures: %s", ip)
        raise HTTPException(429, "Too many failed attempts. Try again later.")


# 인증 실패 기록
def _record_failure(ip: str) -> None:
    if ip not in _failures:
        _failures[ip] = []
    _failures[ip].append(time.time())
    _evict()


# API Key 검증 의존성 (미설정 시 바이패스)
async def require_key(request: Request, key: str | None = Security(_header)) -> str:
    if not settings.api_key:
        return "no-auth"

    client_ip = request.client.host if request.client else "unknown"
    _check_lockout(client_ip)

    if not key or not secrets.compare_digest(key, settings.api_key):
        _record_failure(client_ip)
        logger.warning("Unauthorized API access attempt from %s", client_ip)
        raise HTTPException(403, "Invalid or missing API key")
    return key
