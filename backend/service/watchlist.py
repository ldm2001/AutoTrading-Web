# 워치리스트 파일 저장소
import json
from pathlib import Path
from config import settings

_WATCHLIST_FILE = Path(__file__).resolve().parent.parent / "watchlist.json"

def load() -> list[str]:
    if _WATCHLIST_FILE.exists():
        try:
            return json.loads(_WATCHLIST_FILE.read_text())
        except Exception:
            pass
    return list(settings.symbol_list)

def save(codes: list[str]) -> None:
    _WATCHLIST_FILE.write_text(json.dumps(codes, ensure_ascii=False))

def symbols() -> list[str]:
    return load()
