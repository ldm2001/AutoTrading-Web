# 워치리스트 파일 저장소
import json
from pathlib import Path
from config import settings

# 워치리스트 JSON 파일 경로
_WATCHLIST_FILE = Path(__file__).resolve().parent.parent / "watchlist.json"

# 파일에서 워치리스트 로드 (실패 시 기본 종목 반환)
def load() -> list[str]:
    if _WATCHLIST_FILE.exists():
        try:
            return json.loads(_WATCHLIST_FILE.read_text())
        except Exception:
            pass
    return list(settings.symbol_list)

# 워치리스트를 JSON 파일로 저장
def save(codes: list[str]) -> None:
    _WATCHLIST_FILE.write_text(json.dumps(codes, ensure_ascii=False))

# 현재 워치리스트 종목 반환 (별칭)
def symbols() -> list[str]:
    return load()
