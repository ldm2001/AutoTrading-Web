# 이벤트 기반 백테스터 — 9팩터 스코어링 전략 검증
import logging
from dataclasses import dataclass, field
from datetime import datetime

from service import indicators, smc
from service.strategy import Scorer, BUY_THRESHOLD

logger = logging.getLogger(__name__)


# 백테스트 파라미터
@dataclass
class BacktestConfig:
    take_profit_pct: float = 5.0
    max_hold_bars: int = 20
    fallback_stop_pct: float = 3.0

# 개별 거래 기록
@dataclass
class Trade:
    entry_bar: int
    entry_time: str
    entry_price: float
    exit_bar: int
    exit_time: str
    exit_price: float
    exit_reason: str  # stop | tp | trail
    pnl_pct: float

# 백테스트 결과 집계
@dataclass
class BacktestResult:
    code: str
    total_bars: int = 0
    total_trades: int = 0
    cum_return_pct: float = 0.0
    annualized_pct: float = 0.0
    mdd_pct: float = 0.0
    win_rate_pct: float = 0.0
    risk_reward: float = 0.0
    trades: list[Trade] = field(default_factory=list)

# Scorer 내부 팩터 메서드를 직접 호출 (KIS API 의존 제거)
class BacktestScorer:
    def __init__(self):
        self._s = Scorer()

    # 9팩터 점수 + FVG 손절가 산출
    def score(
        self,
        daily: list[dict],
        candles_15m: list[dict],
        price: int,
    ) -> tuple[float, float | None]:
        ind = indicators.summary(daily)
        smc_candles = candles_15m if candles_15m else daily

        rsi_s, _ = self._s._rsi(ind["rsi"])
        macd_s, _ = self._s._macd(ind["macd"])
        bb_s, _ = self._s._bb(ind["bollinger"])
        vol_s, _ = self._s._vol(daily, price)
        pred_s, _ = self._s._pred(None, price)
        fvg_s, _ = self._s._fvg(daily, price)
        ob_s, _ = self._s._ob(daily, price)
        fvg15_s, _ = self._s._fvg_15m(smc_candles, price)
        str_s, _ = self._s._struct(smc_candles)

        total = rsi_s + macd_s + bb_s + vol_s + pred_s + fvg_s + ob_s + fvg15_s + str_s
        stop = smc.structural_stop(smc_candles, float(price))  # FVG 기반 동적 손절가
        return round(total, 1), stop

def _time_str(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)

# 15분봉 순회하며 진입/청산 시뮬레이션
def run(
    code: str,
    candles_15m: list[dict],
    daily: list[dict],
    cfg: BacktestConfig | None = None,
) -> BacktestResult:
    if cfg is None:
        cfg = BacktestConfig()

    scorer = BacktestScorer()
    n = len(candles_15m)
    trades: list[Trade] = []
    equity = [1.0]

    in_trade = False
    entry_price = 0.0
    entry_bar = 0
    stop_price: float | None = None
    tp_price = 0.0

    for i in range(1, n):
        bar = candles_15m[i]
        bar_time = bar["time"]

        # 청산 체크 (진입보다 먼저)
        if in_trade:
            hold_bars = i - entry_bar

            # 1) FVG 동적 손절
            if stop_price and bar["low"] <= stop_price:
                _close_trade(trades, equity, entry_bar, candles_15m,
                             i, bar_time, entry_price, stop_price, "stop")
                in_trade = False
                continue

            # 2) 고정 % 폴백 손절
            fallback = entry_price * (1 - cfg.fallback_stop_pct / 100)
            if bar["low"] <= fallback:
                _close_trade(trades, equity, entry_bar, candles_15m,
                             i, bar_time, entry_price, fallback, "stop")
                in_trade = False
                continue

            # 3) 익절 (+tp%)
            if bar["high"] >= tp_price:
                _close_trade(trades, equity, entry_bar, candles_15m,
                             i, bar_time, entry_price, tp_price, "tp")
                in_trade = False
                continue

            # 4) 최대 보유 봉수 초과 - 종가 청산
            if hold_bars >= cfg.max_hold_bars:
                _close_trade(trades, equity, entry_bar, candles_15m,
                             i, bar_time, entry_price, float(bar["close"]), "trail")
                in_trade = False
                continue

        # 진입 체크 (미래 참조 금지: candles[:i]만 사용)
        if not in_trade:
            bar_date = bar_time.date().isoformat() if isinstance(bar_time, datetime) else str(bar_time)[:10]
            daily_slice = [c for c in daily if str(c.get("date", ""))[:10] < bar_date]
            if len(daily_slice) < 35:
                continue

            total, stop = scorer.score(daily_slice, candles_15m[:i], bar["close"])

            # 시그널 발생 → 다음 봉 시가에 진입 (슬리피지 반영)
            if total >= BUY_THRESHOLD and i + 1 < n:
                nxt = candles_15m[i + 1]
                entry_price = float(nxt["open"])
                tp_price = entry_price * (1 + cfg.take_profit_pct / 100)
                stop_price = stop
                entry_bar = i + 1
                in_trade = True

    return _metrics(code, n, trades, equity)

# 거래 청산 기록 + 에퀴티 갱신
def _close_trade(
    trades: list[Trade],
    equity: list[float],
    entry_bar: int,
    candles_15m: list[dict],
    exit_bar: int,
    exit_time,
    entry_price: float,
    exit_price: float,
    reason: str,
):
    pnl = (exit_price - entry_price) / entry_price * 100
    trades.append(Trade(
        entry_bar=entry_bar,
        entry_time=_time_str(candles_15m[entry_bar]["time"]),
        entry_price=round(entry_price, 0),
        exit_bar=exit_bar,
        exit_time=_time_str(exit_time),
        exit_price=round(exit_price, 0),
        exit_reason=reason,
        pnl_pct=round(pnl, 2),
    ))
    equity.append(equity[-1] * (1 + pnl / 100))

# 누적수익률, 연환산, MDD, 승률, 손익비 산출
def _metrics(code: str, total_bars: int, trades: list[Trade], equity: list[float]) -> BacktestResult:
    result = BacktestResult(code=code, total_bars=total_bars)
    result.trades = trades
    result.total_trades = len(trades)

    if not trades:
        return result

    # 누적 수익률
    result.cum_return_pct = round((equity[-1] - 1.0) * 100, 2)

    # 연환산 (26봉/일 × 252거래일/년)
    bars_per_year = 26 * 252
    if total_bars > 0 and equity[-1] > 0:
        result.annualized_pct = round(
            ((equity[-1] ** (bars_per_year / total_bars)) - 1.0) * 100, 2
        )

    # MDD
    peak = equity[0]
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        dd = (e - peak) / peak * 100
        mdd = min(mdd, dd)
    result.mdd_pct = round(mdd, 2)

    # 승률
    wins = [t for t in trades if t.pnl_pct > 0]
    result.win_rate_pct = round(len(wins) / len(trades) * 100, 1)

    # 손익비
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    losses = [t for t in trades if t.pnl_pct <= 0]
    avg_loss = abs(sum(t.pnl_pct for t in losses) / len(losses)) if losses else 1
    result.risk_reward = round(avg_win / avg_loss, 2) if avg_loss else 0

    return result
