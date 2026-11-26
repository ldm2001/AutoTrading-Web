# 이벤트 기반 백테스터 — 9팩터 스코어링 전략 검증
import logging
from bisect import bisect_left
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
    exit_reason: str  # stop | tp | trail | end (구간 마지막 봉 종가 강제 청산)
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

    # 9팩터 점수 + FVG 손절가 산출 — prediction은 오프라인 주입용 (None이면 Direction 0점, 라이브와 같은 Scorer.pred·같은 문)
    def val(
        self,
        daily: list[dict],
        candles_15m: list[dict],
        price: int,
        prediction: dict | None = None,
    ) -> tuple[float, float | None]:
        ind = indicators.summary(daily)
        smc_candles = candles_15m if candles_15m else daily

        rsi_s, _ = self._s.rsi(ind["rsi"])
        macd_s, _ = self._s.macd(ind["macd"])
        bb_s, _ = self._s.bb(ind["bollinger"])
        vol_s, _ = self._s.vol(daily, price)
        pred_s, _ = self._s.pred(prediction, price)
        fvg_s, _ = self._s.fvg(daily, price)
        ob_s, _ = self._s.ob(daily, price)
        fvg15_s, _ = self._s.fvg15(smc_candles, price)
        str_s, _ = self._s.struct(smc_candles)

        total = rsi_s + macd_s + bb_s + vol_s + pred_s + fvg_s + ob_s + fvg15_s + str_s
        stop = smc.slprice(smc_candles, float(price))  # FVG 기반 동적 손절가
        return round(total, 1), stop

# datetime → 문자열 변환 유틸리티
def ts(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)

# 봉 날짜보다 앞선 가장 최근 기준일의 예측 — 그 기준일이 직전 lag거래일(일봉 기준)보다 오래됐으면 쓰지 않음
# dates는 정렬된 기준일, daily_slice는 봉 날짜 앞의 일봉 (마지막 행 = 직전 거래일)
def prior(table: dict, dates: list[str], day: str, daily_slice: list[dict], lag: int = 1) -> dict | None:
    k = bisect_left(dates, day)
    if k == 0 or lag < 1 or not daily_slice:
        return None
    base = dates[k - 1]
    floor = str(daily_slice[-min(lag, len(daily_slice))].get("date", ""))[:10]
    return table[base] if base >= floor else None

# 15분봉 순회하며 진입/청산 시뮬레이션 — 채점기는 항상 BacktestScorer (Direction은 Scorer.pred의 문을 지남)
# span=(first, last)면 그 인덱스 구간만 평가 — 채점 이력은 전체 시계열 candles_15m[:i] 그대로
# forecasts={기준일: 예측 dict}면 Direction에 주입 (/api/backtest는 넘기지 않음, 문이 닫혀 있으면 0점이라 주입 없음과 같음)
# scores={봉 번호: (점수, 손절가) 또는 None(일봉 부족)} — 처음 쓰는 봉만 채점해 담고 다시 씀
# 같은 candles_15m·daily·forecasts·lag 호출끼리만 공유 (grid 조합, API의 bt·grid·wf)
def bt(
    code: str,
    candles_15m: list[dict],
    daily: list[dict],
    cfg: BacktestConfig | None = None,
    *,
    span: tuple[int, int] | None = None,
    forecasts: dict | None = None,
    lag: int = 1,
    scores: dict | None = None,
) -> BacktestResult:
    return playback(BacktestScorer(), code, candles_15m, daily, cfg,
                    span=span, forecasts=forecasts, lag=lag, scores=scores)

# 시뮬레이션 본체 — 채점기를 받음. 서비스 코드는 bt로만 부름(채점기 BacktestScorer)
# 다른 채점기(문 없는 Direction)를 넘기는 것은 오프라인 하네스뿐 (offline.injection.LabScorer, tests/test_gate.py가 고정)
def playback(
    scorer: BacktestScorer,
    code: str,
    candles_15m: list[dict],
    daily: list[dict],
    cfg: BacktestConfig | None = None,
    *,
    span: tuple[int, int] | None = None,
    forecasts: dict | None = None,
    lag: int = 1,
    scores: dict | None = None,
) -> BacktestResult:
    if cfg is None:
        cfg = BacktestConfig()

    scores = {} if scores is None else scores
    table = {str(k)[:10]: v for k, v in (forecasts or {}).items()}
    dates = sorted(table)
    n = len(candles_15m)
    first, last = span if span else (0, n)
    first = max(0, min(first, n))
    last = max(first, min(last, n))
    trades: list[Trade] = []
    equity = [1.0]

    in_trade = False
    entry_price = 0.0
    entry_bar = 0
    stop_price: float | None = None
    tp_price = 0.0

    for i in range(max(first, 1), last):
        bar = candles_15m[i]
        bar_time = bar["time"]

        # 청산 체크 (진입보다 먼저)
        if in_trade:
            hold_bars = i - entry_bar

            # 1) FVG 동적 손절 — 시가가 손절가 아래로 갭 하락하면 시가에 체결
            if stop_price and bar["low"] <= stop_price:
                out(trades, equity, entry_bar, candles_15m,
                    i, bar_time, entry_price, min(float(bar["open"]), stop_price), "stop", cfg)
                in_trade = False
                continue

            # 2) 고정 % 폴백 손절 — 갭 하락은 시가 체결
            fallback = entry_price * (1 - cfg.fallback_stop_pct / 100)
            if bar["low"] <= fallback:
                out(trades, equity, entry_bar, candles_15m,
                    i, bar_time, entry_price, min(float(bar["open"]), fallback), "stop", cfg)
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
            if i not in scores:
                bar_date = bar_time.date().isoformat() if isinstance(bar_time, datetime) else str(bar_time)[:10]
                daily_slice = [c for c in daily if str(c.get("date", ""))[:10] < bar_date]
                mark = None
                if len(daily_slice) >= 35:
                    prediction = prior(table, dates, bar_date, daily_slice, lag) if dates else None
                    mark = scorer.val(daily_slice, candles_15m[:i], bar["close"], prediction)
                scores[i] = mark
            if scores[i] is None:
                continue
            total, stop = scores[i]

            # 시그널 발생 → 다음 봉 시가에 진입 (슬리피지 반영, 다음 봉이 구간 안일 때만)
            if total >= cfg.buy_threshold and i + 1 < last:
                nxt = candles_15m[i + 1]
                entry_price = bpx(float(nxt["open"]), cfg)
                tp_price = entry_price * (1 + cfg.take_profit_pct / 100)
                stop_price = stop
                entry_bar = i + 1
                in_trade = True

    # 구간 끝까지 들고 있던 포지션 — 버리지 않고 마지막 봉 종가로 강제 청산해 집계
    if in_trade:
        tail = candles_15m[last - 1]
        out(trades, equity, entry_bar, candles_15m,
            last - 1, tail["time"], entry_price, float(tail["close"]), "end", cfg)

    return stat(code, last - first, trades, equity, candles_15m[first:last], cfg)

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
    if any(t.exit_reason == "end" for t in trades):
        result.validation_warnings.append("구간 끝까지 보유한 포지션은 마지막 봉 종가로 강제 청산해 집계했습니다(사유 end).")
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
    scores: dict | None = None,
) -> list[dict]:
    # 봉 점수는 조합과 무관 — 표 하나를 모든 조합이 같이 씀 (넘겨받으면 호출자와도 공유)
    scores = {} if scores is None else scores
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
                    scores=scores,
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
    scores: dict | None = None,
) -> list[dict]:
    if windows <= 0 or not candles_15m:
        return []

    # 구간마다 같은 전체 시계열로 채점하므로 표 하나를 같이 씀
    scores = {} if scores is None else scores
    n = len(candles_15m)
    size = max(1, n // windows)
    rows: list[dict] = []
    for idx in range(windows):
        start = idx * size
        end = n if idx == windows - 1 else min(n, (idx + 1) * size)
        if start >= end:
            continue
        # 구간을 잘라 넘기지 않고 전체 시계열의 인덱스 범위로 평가 (앞 구간 15분봉도 채점 이력)
        result = bt(code, candles_15m, daily, cfg, span=(start, end), scores=scores)
        rows.append({
            "window": idx + 1,
            "start_time": ts(candles_15m[start]["time"]),
            "end_time": ts(candles_15m[end - 1]["time"]),
            "total_bars": result.total_bars,
            "total_trades": result.total_trades,
            "cum_return_pct": result.cum_return_pct,
            "excess_return_pct": result.excess_return_pct,
            "mdd_pct": result.mdd_pct,
        })
    return rows
