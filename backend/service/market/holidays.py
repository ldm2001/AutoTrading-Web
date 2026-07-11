# 한국 증시(KRX) 휴장일 — 매년 말 다음 해 목록 수동 갱신 (음력·대체공휴일 변동)
import logging
from datetime import date

logger = logging.getLogger(__name__)

# 연도별 KRX 휴장일 (월, 일) — 주말 제외 실제 휴장일 기준 (KRX 공고 대조 2026-07-11)
# 2026: 신정 1/1 · 설연휴 2/16-18 · 삼일절 대체 3/2 · 근로자의날 5/1 · 어린이날 5/5
#       석탄일 대체 5/25 · 광복절 대체 8/17 · 추석 9/24-25 · 개천절 대체 10/5
#       한글날 10/9 · 성탄절 12/25 · 연말휴장 12/31
_KRX_HOLIDAYS: dict[int, set[tuple[int, int]]] = {
    2026: {
        (1, 1),
        (2, 16), (2, 17), (2, 18),
        (3, 2),
        (5, 1), (5, 5), (5, 25),
        (8, 17),
        (9, 24), (9, 25),
        (10, 5), (10, 9),
        (12, 25), (12, 31),
    },
}

_warned_years: set[int] = set()

# 개장일 여부 (주말/휴장일 제외) — 미등록 연도는 주말만 체크하고 1회 경고
def mkt(d: date | None = None) -> bool:
    d = d or date.today()
    if d.weekday() >= 5:
        return False
    holidays = _KRX_HOLIDAYS.get(d.year)
    if holidays is None:
        if d.year not in _warned_years:
            _warned_years.add(d.year)
            logger.warning(f"KRX holidays unregistered for {d.year}: weekend-only check")
        return True
    return (d.month, d.day) not in holidays
