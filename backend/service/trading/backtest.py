# 이벤트 기반 백테스터 — 9팩터 스코어링 전략 검증
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime

from service.market import indicators, smc
from service.trading.strategy import Scorer, BUY_THRESHOLD

logger = logging.getLogger(__name__)


# 백테스트 파라미터
@dataclass
class BacktestConfig:
    take_profit_pct: float = 5.0
    max_hold_bars: int = 20
    fallback_stop_pct: float = 3.0
    buy_threshold: float = BUY_THRESHOLD
    fee_bps: float = 1.5
    sell_tax_bps: float = 23.0
    slippage_bps: float = 5.0
    spread_bps: float = 4.0

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
    cost_bps: float = 0.0

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
    buy_hold_return_pct: float | None = None
    excess_return_pct: float | None = None
    avg_trade_cost_bps: float = 0.0
    validation_warnings: list[str] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

# API 없이 로컬 데이터만으로 9팩터 스코어링
class BacktestScorer:
    # Scorer 내부 인스턴스 생성
    def __init__(self):
        self._s = Scorer()

    # 9팩터 점수 + FVG 손절가 산출
    def val(
        self,
        daily: list[dict],
        candles_15m: list[dict],
        price: int,
    ) -> tuple[float, float | None]:
        ind = indicators.summary(daily)
        smc_candles = candles_15m if candles_15m else daily

        rsi_s, _ = self._s.rsi(ind["rsi"])
        macd_s, _ = self._s.macd(ind["macd"])
        bb_s, _ = self._s.bb(ind["bollinger"])
        vol_s, _ = self._s.vol(daily, price)
        pred_s, _ = self._s.pred(None, price)
        fvg_s, _ = self._s.fvg(daily, price)
        ob_s, _ = self._s.ob(daily, price)
        fvg15_s, _ = self._s.fvg15(smc_candles, price)
        str_s, _ = self._s.struct(smc_candles)

        total = rsi_s + macd_s + bb_s + vol_s + pred_s + fvg_s + ob_s + fvg15_s + str_s
        stop = smc.stop(smc_candles, float(price))  # FVG 기반 동적 손절가
        return round(total, 1), stop

# datetime → 문자열 변환 유틸리티
def ts(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)

# 15분봉 순회하며 진입/청산 시뮬레이션
def bt(
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
                out(trades, equity, entry_bar, candles_15m,
                    i, bar_time, entry_price, stop_price, "stop", cfg)
                in_trade = False
                continue

            # 2) 고정 % 폴백 손절
            fallback = entry_price * (1 - cfg.fallback_stop_pct / 100)
            if bar["low"] <= fallback:
                out(trades, equity, entry_bar, candles_15m,
                    i, bar_time, entry_price, fallback, "stop", cfg)
                in_trade = False
                continue

            # 3) 익절 (+tp%)
            if bar["high"] >= tp_price:
                out(trades, equity, entry_bar, candles_15m,
                    i, bar_time, entry_price, tp_price, "tp", cfg)
                in_trade = False
                continue

            # 4) 최대 보유 봉수 초과 - 종가 청산
            if hold_bars >= cfg.max_hold_bars:
                out(trades, equity, entry_bar, candles_15m,
                    i, bar_time, entry_price, float(bar["close"]), "trail", cfg)
                in_trade = False
                continue

        # 진입 체크 (미래 참조 금지: candles[:i]만 사용)
        if not in_trade:
            bar_date = bar_time.date().isoformat() if isinstance(bar_time, datetime) else str(bar_time)[:10]
            daily_slice = [c for c in daily if str(c.get("date", ""))[:10] < bar_date]
            if len(daily_slice) < 35:
                continue

            total, stop = scorer.val(daily_slice, candles_15m[:i], bar["close"])

            # 시그널 발생 → 다음 봉 시가에 진입 (슬리피지 반영)
            if total >= cfg.buy_threshold and i + 1 < n:
                nxt = candles_15m[i + 1]
                entry_price = bpx(float(nxt["open"]), cfg)
                tp_price = entry_price * (1 + cfg.take_profit_pct / 100)
                stop_price = stop
                entry_bar = i + 1
                in_trade = True

    return stat(code, n, trades, equity, candles_15m, cfg)

# 거래 청산 기록 + 에퀴티 갱신
def out(
    trades: list[Trade],
    equity: list[float],
    entry_bar: int,
    candles_15m: list[dict],
    exit_bar: int,
    exit_time,
    entry_price: float,
    exit_price: float,
    reason: str,
    cfg: BacktestConfig | None = None,
):
    cfg = cfg or BacktestConfig(fee_bps=0, sell_tax_bps=0, slippage_bps=0, spread_bps=0)
    exit_price = spx(exit_price, cfg)
    pnl = (exit_price - entry_price) / entry_price * 100
    trades.append(Trade(
        entry_bar=entry_bar,
        entry_time=ts(candles_15m[entry_bar]["time"]),
        entry_price=round(entry_price, 0),
        exit_bar=exit_bar,
        exit_time=ts(exit_time),
        exit_price=round(exit_price, 0),
        exit_reason=reason,
        pnl_pct=round(pnl, 2),
        cost_bps=round(tcost(cfg), 2),
    ))
    equity.append(equity[-1] * (1 + pnl / 100))

# 누적수익률, 연환산, MDD, 승률, 손익비 산출
def stat(
    code: str,
    total_bars: int,
    trades: list[Trade],
    equity: list[float],
    candles_15m: list[dict],
    cfg: BacktestConfig,
) -> BacktestResult:
    result = BacktestResult(code=code, total_bars=total_bars)
    result.trades = trades
    result.total_trades = len(trades)
    result.validation_warnings = risks(candles_15m)
    result.buy_hold_return_pct = bh(candles_15m, cfg)
    if result.buy_hold_return_pct is not None:
        result.excess_return_pct = round(result.cum_return_pct - result.buy_hold_return_pct, 2)

    if not trades:
        return result

    # 누적 수익률
    result.cum_return_pct = round((equity[-1] - 1.0) * 100, 2)
    if result.buy_hold_return_pct is not None:
        result.excess_return_pct = round(result.cum_return_pct - result.buy_hold_return_pct, 2)

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
    result.avg_trade_cost_bps = round(
        sum(t.cost_bps for t in trades) / len(trades), 2
    ) if trades else 0

    return result


def bcost(cfg: BacktestConfig) -> float:
    return cfg.fee_bps + cfg.slippage_bps + cfg.spread_bps / 2


def scost(cfg: BacktestConfig) -> float:
    return cfg.fee_bps + cfg.sell_tax_bps + cfg.slippage_bps + cfg.spread_bps / 2


def tcost(cfg: BacktestConfig) -> float:
    return bcost(cfg) + scost(cfg)


def bpx(price: float, cfg: BacktestConfig) -> float:
    return price * (1 + bcost(cfg) / 10_000)


def spx(price: float, cfg: BacktestConfig) -> float:
    return price * (1 - scost(cfg) / 10_000)


def bh(candles_15m: list[dict], cfg: BacktestConfig) -> float | None:
    if len(candles_15m) < 2:
        return None
    entry = bpx(float(candles_15m[0]["open"]), cfg)
    exit_ = spx(float(candles_15m[-1]["close"]), cfg)
    return round((exit_ - entry) / entry * 100, 2)


def risks(candles_15m: list[dict]) -> list[str]:
    warnings: list[str] = [
        "단일 포지션·% 수익 모델 — 실봇의 자금배분(buy_percent)·다종목 보유·현금 갱신·체결 지연·미체결은 미반영",
    ]
    if len(candles_15m) < 500:
        warnings.append("15분봉 표본이 작아 결과 신뢰도가 낮습니다.")
    dates = {
        c["time"].date().isoformat()
        for c in candles_15m
        if isinstance(c.get("time"), datetime)
    }
    if len(dates) < 20:
        warnings.append("검증 거래일 수가 부족해 국면별 안정성을 판단하기 어렵습니다.")
    return warnings


def grid(
    code: str,
    candles_15m: list[dict],
    daily: list[dict],
    cfg: BacktestConfig,
    *,
    buy_thresholds: list[float],
    take_profit_pcts: list[float],
    stop_pcts: list[float],
) -> list[dict]:
    rows: list[dict] = []
    for threshold in buy_thresholds:
        for tp in take_profit_pcts:
            for stop in stop_pcts:
                result = bt(
                    code,
                    candles_15m,
                    daily,
                    replace(
                        cfg,
                        buy_threshold=threshold,
                        take_profit_pct=tp,
                        fallback_stop_pct=stop,
                    ),
                )
                rows.append({
                    "buy_threshold": threshold,
                    "take_profit_pct": tp,
                    "fallback_stop_pct": stop,
                    "total_trades": result.total_trades,
                    "cum_return_pct": result.cum_return_pct,
                    "mdd_pct": result.mdd_pct,
                    "excess_return_pct": result.excess_return_pct,
                })
    return rows


def wf(
    code: str,
    candles_15m: list[dict],
    daily: list[dict],
    cfg: BacktestConfig,
    *,
    windows: int = 6,
) -> list[dict]:
    if windows <= 0 or not candles_15m:
        return []

    n = len(candles_15m)
    size = max(1, n // windows)
    rows: list[dict] = []
    for idx in range(windows):
        start = idx * size
        end = n if idx == windows - 1 else min(n, (idx + 1) * size)
        chunk = candles_15m[start:end]
        if not chunk:
            continue
        result = bt(code, chunk, daily, cfg)
        rows.append({
            "window": idx + 1,
            "start_time": ts(chunk[0]["time"]),
            "end_time": ts(chunk[-1]["time"]),
            "total_bars": result.total_bars,
            "total_trades": result.total_trades,
            "cum_return_pct": result.cum_return_pct,
            "excess_return_pct": result.excess_return_pct,
            "mdd_pct": result.mdd_pct,
        })
    return rows
