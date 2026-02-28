# 백테스트 API 라우터
import datetime
import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from service.backtest import BacktestConfig, run
from service.candle_store import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest")


# 백테스트 요청 파라미터
class BacktestRequest(BaseModel):
    code: str
    days: int = 30
    take_profit_pct: float = 5.0
    max_hold_bars: int = 20


@router.post("")
async def backtest(req: BacktestRequest):
    code = req.code.zfill(6)

    # 15분봉 — CandleStore CSV에서 로드
    candles_15m = store.load_days(code, interval=15, days=req.days)
    if len(candles_15m) < 50:
        raise HTTPException(
            400,
            f"15분봉 데이터 부족 ({len(candles_15m)}개). "
            "CandleStore에 데이터가 축적된 후 사용 가능합니다.",
        )

    # 일봉 — FDR로 수집 (KIS API 호출 없음)
    try:
        import FinanceDataReader as fdr
        end = datetime.date.today()
        start = end - datetime.timedelta(days=req.days + 90)
        df = fdr.DataReader(code, start.isoformat(), end.isoformat())
        daily = []
        for dt, row in df.iterrows():
            daily.append({
                "date":   str(dt)[:10],
                "open":   int(row["Open"]),
                "high":   int(row["High"]),
                "low":    int(row["Low"]),
                "close":  int(row["Close"]),
                "volume": int(row["Volume"]),
            })
    except Exception as e:
        raise HTTPException(502, f"일봉 데이터 수집 실패: {e}")

    if len(daily) < 35:
        raise HTTPException(400, f"일봉 데이터 부족 ({len(daily)}개, 최소 35개 필요)")

    # 설정 적용 후 백테스트 실행
    cfg = BacktestConfig(
        take_profit_pct=req.take_profit_pct,
        max_hold_bars=req.max_hold_bars,
    )
    result = run(code, candles_15m, daily, cfg)

    # 결과 직렬화
    return {
        "code":            result.code,
        "total_bars":      result.total_bars,
        "total_trades":    result.total_trades,
        "cum_return_pct":  result.cum_return_pct,
        "annualized_pct":  result.annualized_pct,
        "mdd_pct":         result.mdd_pct,
        "win_rate_pct":    result.win_rate_pct,
        "risk_reward":     result.risk_reward,
        "trades":          [asdict(t) for t in result.trades],
    }
