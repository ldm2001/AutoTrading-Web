# 동적 손절 판정 — FVG 구조적 손절가 우선, 폴백으로 고정 %
# 시세 조회 실패는 호출부로 전파 (fail-open 금지 — bot.risk가 카운터/경보 처리)
from service.trading.ports import Quotes

async def stop_loss(
    quotes: Quotes, code: str, avg_price: int,
    structural_price: float | None = None,
    fallback_pct: float = -3.0,
) -> tuple[bool, float]:
    current = await quotes.raw(code)
    pnl = (current - avg_price) / avg_price * 100

    # 구조적 손절가가 있으면 그 가격 하회 시 손절
    if structural_price and current < structural_price:
        return True, pnl

    # 폴백: 고정 % 손절
    return pnl <= fallback_pct, pnl
