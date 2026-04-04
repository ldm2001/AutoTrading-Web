# 워치리스트 파일 저장소 (mtime 캐싱으로 불필요한 디스크 I/O 제거)
import json
from pathlib import Path
from config import settings

_WATCHLIST_FILE = Path(__file__).resolve().parent.parent / "watchlist.json"

# 인메모리 캐시 — 파일 mtime이 바뀔 때만 다시 읽음
_cached_list: list[str] | None = None
_cached_mtime: float = 0.0


# 파일에서 워치리스트 로드 (실패 시 기본 종목 반환)
def load() -> list[str]:
    global _cached_list, _cached_mtime

    if not _WATCHLIST_FILE.exists():
        _cached_list = None
        _cached_mtime = 0.0
        return list(settings.symbol_list)

    try:
        current_mtime = _WATCHLIST_FILE.stat().st_mtime
    except OSError:
        return _cached_list if _cached_list is not None else list(settings.symbol_list)

    # mtime 변경 없으면 캐시 반환
    if _cached_list is not None and current_mtime == _cached_mtime:
        return list(_cached_list)

    try:
        data = json.loads(_WATCHLIST_FILE.read_text())
        _cached_list = data
        _cached_mtime = current_mtime
        return list(data)
    except Exception:
        return _cached_list if _cached_list is not None else list(settings.symbol_list)


# 워치리스트를 JSON 파일로 저장
def save(codes: list[str]) -> None:
    global _cached_list, _cached_mtime
    _WATCHLIST_FILE.write_text(json.dumps(codes, ensure_ascii=False))
    _cached_list = list(codes)
    try:
        _cached_mtime = _WATCHLIST_FILE.stat().st_mtime
    except OSError:
        _cached_mtime = 0.0


# 현재 워치리스트 종목 반환
def symbols() -> list[str]:
    return load()
