import time
from typing import Any

# ttl 캐시
class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    # 유효한 캐시만 반환
    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        exp, value = entry
        if time.time() < exp:
            return value
        del self._data[key]
        return None

    # 값을 ttl 함께 저장
    def set(self, key: str, value: Any, ttl: float) -> None:
        self._data[key] = (time.time() + ttl, value)

    # 접두사 기준으로 캐시를 무효화
    def invalidate(self, *prefixes: str) -> None:
        keys = [key for key in self._data if any(key.startswith(prefix) for prefix in prefixes)]
        for key in keys:
            del self._data[key]

    # 전체 캐시 정리
    def clear(self) -> None:
        self._data.clear()
