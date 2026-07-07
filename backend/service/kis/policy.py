# 읽기 경로 보호 + 재시도 + stale 폴백 정책
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


# 읽기 경로 보호 정책
class Policy:
    # 캐시 저장소 및 재시도 대기 시간 초기화
    def __init__(self) -> None:
        self._last: dict[str, object] = {}
        self._wait = (0.2, 0.5)

    # 마지막 성공 값을 반환
    def last(self, key: str) -> T | None:
        value = self._last.get(key)
        return value if value is not None else None

    # 마지막 성공 값을 저장
    def keep(self, key: str, value: T) -> T:
        self._last[key] = value
        return value

    # 읽기 호출을 보호
    async def safe(
        self,
        key: str,
        slot: Callable[[], Awaitable[T]],
        *,
        mark: str,
        stale: bool,
        tries: int = 2,
    ) -> T:
        err_last: Exception | None = None

        # 최대 tries+1회 시도
        for turn in range(tries + 1):
            try:
                value = await slot()
                return self.keep(key, value)
            except Exception as err:
                err_last = err
                code = None
                retry = False

                # HTTP 상태 코드 기반 재시도 판단

                if isinstance(err, httpx.HTTPStatusError):
                    code = err.response.status_code
                    retry = code == 429 or 500 <= code < 600
                elif isinstance(
                    err,
                    (
                        httpx.TimeoutException,
                        httpx.NetworkError,
                        httpx.TransportError,
                    ),
                ):
                    retry = True

                logger.warning(
                    "Read fail mark=%s key=%s turn=%s code=%s retry=%s err=%s",
                    mark,
                    key,
                    turn + 1,
                    code,
                    retry,
                    err,
                )

                if not retry or turn >= tries:
                    break
                await asyncio.sleep(self._wait[min(turn, len(self._wait) - 1)])

        # stale 허용 시 마지막 성공 값으로 폴백
        if stale:
            cached = self.last(key)
            if cached is not None:
                logger.warning("Read stale mark=%s key=%s", mark, key)
                return cached

        if err_last is not None:
            raise err_last
        raise RuntimeError(f"Read empty: {mark}:{key}")


policy = Policy()
