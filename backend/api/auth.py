# API Key 인증 의존성 + brute-force lockout
import time
import secrets
import logging
from collections import defaultdict
from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from config import settings

logger = logging.getLogger(__name__)

_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# IP별 실패 추적 (in-memory)
_failures: dict[str, list[float]] = defaultdict(list)
_LOCKOUT_THRESHOLD = 5
_LOCKOUT_WINDOW = 900  # 15분


def _check_lockout(ip: str) -> None:
    now = time.time()
    _failures[ip] = [t for t in _failures[ip] if now - t < _LOCKOUT_WINDOW]
    if len(_failures[ip]) >= _LOCKOUT_THRESHOLD:
        logger.warning("IP locked out due to repeated auth failures: %s", ip)
        raise HTTPException(429, "Too many failed attempts. Try again later.")


def _record_failure(ip: str) -> None:
    _failures[ip].append(time.time())


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
