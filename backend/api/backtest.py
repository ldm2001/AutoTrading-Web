# 백테스트 API 라우터
import asyncio
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from service.trading.backtest import BacktestConfig, BacktestResult, bt, grid, wf
from service.trading.research import wftab
from service.market.candle_store import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest")

# 백테스트 전용 스레드 1개 — 기본 실행기는 event_bus가 쓰므로 공유하지 않는다
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest")
# 실행 중인 작업 — 동시 1건 제한 (요청이 끊겨도 끝날 때까지 유지)
_job: asyncio.Future | None = None


# 백테스트 요청 파라미터
class BacktestRequest(BaseModel):
    code: str
    days: int = Field(default=365, ge=7, le=730)
    take_profit_pct: float = Field(default=5.0, ge=0.1, le=50.0)
    max_hold_bars: int = Field(default=20, ge=1, le=100)
    fallback_stop_pct: float = Field(default=3.0, ge=0.1, le=50.0)
    buy_threshold: float = Field(default=55.0, ge=-100.0, le=100.0)
    fee_bps: float = Field(default=1.5, ge=0.0, le=100.0)
    sell_tax_bps: float = Field(default=23.0, ge=0.0, le=100.0)
    slippage_bps: float = Field(default=5.0, ge=0.0, le=100.0)
    spread_bps: float = Field(default=4.0, ge=0.0, le=100.0)
    include_validation: bool = True


# 일봉 — FDR로 수집 (KIS API 호출 없음), 실패는 502
def dayrows(code: str, days: int) -> list[dict]:
    try:
        import FinanceDataReader as fdr
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days + 90)
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
    except Exception:
        raise HTTPException(502, "일봉 데이터 수집 실패")
    return daily


# 일봉 수집 + bt + grid + wf — 백테스트 스레드에서 실행 (파일 쓰기 없음, 라이브 상태 미접촉)
def work(req: BacktestRequest, code: str, candles_15m: list[dict]) -> tuple[BacktestResult, dict | None]:
    daily = dayrows(code, req.days)
    if len(daily) < 35:
        raise HTTPException(400, f"일봉 데이터 부족 ({len(daily)}개, 최소 35개 필요)")

    # 설정 적용 후 백테스트 실행
    cfg = BacktestConfig(
        take_profit_pct=req.take_profit_pct,
        max_hold_bars=req.max_hold_bars,
        fallback_stop_pct=req.fallback_stop_pct,
        buy_threshold=req.buy_threshold,
        fee_bps=req.fee_bps,
        sell_tax_bps=req.sell_tax_bps,
        slippage_bps=req.slippage_bps,
        spread_bps=req.spread_bps,
    )
    # 봉 점수 표 하나를 bt·grid 27조합·wf가 같이 씀 (같은 봉을 다시 채점하지 않음, 결과 불변)
    scores: dict = {}
    result = bt(code, candles_15m, daily, cfg, scores=scores)
    validation = None
    if req.include_validation:
        validation = {
            "parameter_stability": grid(
                code,
                candles_15m,
                daily,
                cfg,
                buy_thresholds=[req.buy_threshold - 5, req.buy_threshold, req.buy_threshold + 5],
                take_profit_pcts=[
                    max(0.1, req.take_profit_pct - 1),
                    req.take_profit_pct,
                    req.take_profit_pct + 1,
                ],
                stop_pcts=[
                    max(0.1, req.fallback_stop_pct - 1),
                    req.fallback_stop_pct,
                    req.fallback_stop_pct + 1,
                ],
                scores=scores,
            ),
            "walk_forward": wf(code, candles_15m, daily, cfg, scores=scores),
        }
    return result, validation


# 끝난 작업의 예외 회수 — 요청이 끊긴 뒤 실패해도 "exception was never retrieved" 없이 로그만
def reaper(fut: asyncio.Future) -> None:
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None and not isinstance(exc, HTTPException):
        logger.warning("백테스트 작업 실패: %r", exc)


@router.post("")
async def btapi(req: BacktestRequest):
    global _job
    # 동시 1건 — 이 검사부터 작업 등록까지 await가 없어 다른 요청이 끼어들 수 없다
    if _job is not None and not _job.done():
        raise HTTPException(429, "백테스트가 이미 실행 중입니다")
    code = req.code.zfill(6)

    # 15분봉 — CandleStore CSV에서 로드 (틱 기록과 같은 루프 스레드에서 읽기)
    candles_15m = store.span(code, interval=15, days=req.days)
    if len(candles_15m) < 50:
        raise HTTPException(
            400,
            f"15분봉 데이터 부족 ({len(candles_15m)}개). "
            "CandleStore에 데이터가 축적된 후 사용 가능합니다.",
        )

    job = asyncio.get_running_loop().run_in_executor(_pool, work, req, code, candles_15m)
    job.add_done_callback(reaper)
    _job = job
    # 요청이 끊겨도 작업과 슬롯은 유지 — 끝날 때까지 새 요청은 429
    result, validation = await asyncio.shield(job)

    # walk-forward 기록 — research JSONL 쓰기는 루프 스레드에서
    validation_log_count = None
    if validation is not None:
        try:
            validation_log_count = wftab(code, {
                "days": req.days,
                "total_bars": result.total_bars,
                "total_trades": result.total_trades,
                "cum_return_pct": result.cum_return_pct,
                "excess_return_pct": result.excess_return_pct,
                "walk_forward": validation["walk_forward"],
            })
        except Exception as exc:
            logger.warning("walk-forward 기록 저장 실패: %s", exc)

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
        "buy_hold_return_pct": result.buy_hold_return_pct,
        "excess_return_pct": result.excess_return_pct,
        "avg_trade_cost_bps": result.avg_trade_cost_bps,
        "validation_warnings": result.validation_warnings,
        "validation": validation,
        "validation_log_count": validation_log_count,
        "cost_assumptions": {
            "fee_bps": req.fee_bps,
            "sell_tax_bps": req.sell_tax_bps,
            "slippage_bps": req.slippage_bps,
            "spread_bps": req.spread_bps,
        },
        "trades":          [asdict(t) for t in result.trades],
    }
