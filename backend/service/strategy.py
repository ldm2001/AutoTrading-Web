# 멀티팩터 매매 전략 스코어러
import asyncio
import logging
import time

from service.kis import kis
from service import indicators
from service import smc

logger = logging.getLogger(__name__)

# 팩터별 최대 점수 (합계 100점)
W_RSI      = 15 # 방향성 참고
W_MACD     = 15 # 모멘텀 확인
W_BB       = 10 # 밴드 위치 참고
W_VOL      = 12 # 변동성 돌파
W_PREDICT  = 10 # 5일 예측 -> 방향성 필터
W_FVG      = 8  # 일봉 FVG 근접도
W_OB       = 7  # 일봉 OB 지지/저항
W_FVG_15M  = 15 # 15분봉 FVG — 실제 진입 트리거
W_STRUCT   = 8  # BOS/CHoCH 구조 점수

# 매수/매도 진입 임계값
BUY_THRESHOLD  = 55
SELL_THRESHOLD = -40

# 평가 캐시
_cache: dict[str, tuple[float, dict]] = {}
_TTL   = 120

class Scorer:

    # RSI 점수화 (-25 ~ +25)
    def _rsi(self, val: float | None) -> tuple[float, str]:
        if val is None:
            return 0, "RSI 데이터 부족"
        if val <= 25:
            return W_RSI, f"RSI {val:.1f} (극단 과매도 → 강력 매수)"
        if val <= 30:
            return W_RSI * 0.8, f"RSI {val:.1f} (과매도 → 매수)"
        if val <= 40:
            return W_RSI * 0.4, f"RSI {val:.1f} (약세 반등 가능)"
        if val <= 60:
            return 0, f"RSI {val:.1f} (중립)"
        if val <= 70:
            return -W_RSI * 0.4, f"RSI {val:.1f} (과열 주의)"
        if val <= 80:
            return -W_RSI * 0.8, f"RSI {val:.1f} (과매수 → 매도)"
        return -W_RSI, f"RSI {val:.1f} (극단 과매수 → 강력 매도)"

    # MACD 점수화 (-25 ~ +25)
    def _macd(self, data: dict | None) -> tuple[float, str]:
        if data is None:
            return 0, "MACD 데이터 부족"
        hist       = data["histogram"]
        macd_val   = data["macd"]
        signal_val = data["signal"]
        # 골든크로스 -> 매수
        if hist > 0 and macd_val > signal_val:
            strength = min(abs(hist) / max(abs(signal_val), 1) * 10, 1.0)
            return W_MACD * strength, f"MACD 골든크로스 (hist={hist:+.1f})"
        # 데드크로스 -> 매도
        if hist < 0 and macd_val < signal_val:
            strength = min(abs(hist) / max(abs(signal_val), 1) * 10, 1.0)
            return -W_MACD * strength, f"MACD 데드크로스 (hist={hist:+.1f})"
        return 0, f"MACD 중립 (hist={hist:+.1f})"

    # 볼린저밴드 점수화 (-20 ~ +20)
    def _bb(self, data: dict | None) -> tuple[float, str]:
        if data is None:
            return 0, "볼린저밴드 데이터 부족"
        price      = data["current_price"]
        upper      = data["upper"]
        lower      = data["lower"]
        band_width = upper - lower
        if band_width == 0:
            return 0, "밴드 폭 0"
        # 하단 이탈 -> 매수 (반등 기대)
        if price <= lower:
            return W_BB, f"볼린저 하단 이탈 ({price:,.0f} ≤ {lower:,.0f})"
        # 하단 근접
        if price < lower + band_width * 0.2:
            return W_BB * 0.6, "볼린저 하단 근접"
        # 상단 이탈 -> 매도 (과열)
        if price >= upper:
            return -W_BB, f"볼린저 상단 이탈 ({price:,.0f} ≥ {upper:,.0f})"
        # 상단 근접
        if price > upper - band_width * 0.2:
            return -W_BB * 0.6, "볼린저 상단 근접"
        return 0, "볼린저 중립 (밴드 내)"

    # 변동성 돌파 점수화 (-15 ~ +15)
    def _vol(self, candles: list[dict], price: int) -> tuple[float, str]:
        if len(candles) < 2:
            return 0, "캔들 데이터 부족"
        prev       = candles[-2]
        today_open = candles[-1]["open"]
        prev_range = prev["high"] - prev["low"]
        target     = today_open + prev_range * 0.5
        if price >= target:
            excess   = (price - target) / target * 100
            strength = min(excess / 2, 1.0)
            return W_VOL * max(strength, 0.5), f"변동성 돌파 (목표 {target:,.0f} < 현재 {price:,})"
        gap_pct = (target - price) / target * 100
        if gap_pct < 0.5:
            return W_VOL * 0.3, f"변동성 돌파 근접 (목표 {target:,.0f}, 현재 {price:,})"
        return 0, f"변동성 미돌파 (목표 {target:,.0f})"

    # 일봉 FVG 근접도 점수화 (-8 ~ +8)
    def _fvg(self, candles: list[dict], price: int) -> tuple[float, str]:
        try:
            s, r = smc.fvg_score(candles, float(price))
            return round(s * (W_FVG / 8), 1), r
        except Exception:
            return 0.0, "FVG 계산 오류"

    # OB 지지/저항 점수화 (-7 ~ +7)
    def _ob(self, candles: list[dict], price: int) -> tuple[float, str]:
        try:
            s, r = smc.ob_score(candles, float(price))
            return round(s * (W_OB / 7), 1), r
        except Exception:
            return 0.0, "OB 계산 오류"

    # 15분봉 FVG 점수화 (-15 ~ +15) — 실제 진입 트리거
    def _fvg_15m(self, candles_15m: list[dict], price: int) -> tuple[float, str]:
        try:
            s, r, _ = smc.fvg_intraday(candles_15m, float(price))
            return round(s * (W_FVG_15M / 10), 1), r
        except Exception:
            return 0.0, "15m FVG 계산 오류"

    # BOS/CHoCH 구조 점수화 (-8 ~ +8)
    def _struct(self, candles: list[dict]) -> tuple[float, str]:
        try:
            s, r = smc.structure_score(candles)
            return round(s * (W_STRUCT / 5), 1), r
        except Exception:
            return 0.0, "구조 분석 오류"

    # Transformer 예측 → 방향성 필터 (-10 ~ +10)
    def _pred(self, prediction: dict | None, price: int) -> tuple[float, str]:
        if prediction is None:
            return 0, "예측 데이터 없음"
        preds = prediction.get("predictions", [])
        if not preds:
            return 0, "예측 결과 없음"
        last_close  = preds[-1]["close"]
        change_pct  = (last_close - price) / price * 100
        uptrend_cnt = 0
        prev        = price
        for p in preds:
            if p["close"] > prev:
                uptrend_cnt += 1
            prev = p["close"]
        # 방향성 필터: 강한 추세만 반영 (±3% 이상)
        if change_pct > 3 and uptrend_cnt >= 3:
            return W_PREDICT, f"방향 필터: 상승 +{change_pct:.1f}% (5일)"
        if change_pct > 1:
            return W_PREDICT * 0.5, f"방향 필터: 약상승 +{change_pct:.1f}%"
        if change_pct < -3 and uptrend_cnt <= 1:
            return -W_PREDICT, f"방향 필터: 하락 {change_pct:.1f}% (5일)"
        if change_pct < -1:
            return -W_PREDICT * 0.5, f"방향 필터: 약하락 {change_pct:.1f}%"
        return 0, f"방향 필터: 중립 {change_pct:+.1f}%"

    # 종목 종합 평가 (멀티팩터 + 15분봉 FVG 앙상블)
    # fast=True: 1단계 스크리닝용 (15분봉 스킵 → API 호출 2건으로 축소)
    async def evaluate(self, code: str, prediction: dict | None = None, fast: bool = False) -> dict:
        if prediction is None and not fast:
            cached = _cache.get(code)
            if cached and time.time() < cached[0]:
                return cached[1]
        try:
            if fast:
                candles, price_info = await asyncio.gather(
                    kis.daily(code), kis.price(code),
                )
                candles_15m = []
            else:
                candles, price_info, candles_15m = await asyncio.gather(
                    kis.daily(code), kis.price(code), kis.candles_15m(code),
                )
            current_price = price_info["price"]
            ind = indicators.summary(candles)

            smc_candles = candles_15m if candles_15m else candles

            rsi_s,    rsi_r    = self._rsi(ind["rsi"])
            macd_s,   macd_r   = self._macd(ind["macd"])
            bb_s,     bb_r     = self._bb(ind["bollinger"])
            vol_s,    vol_r    = self._vol(candles, current_price)
            pred_s,   pred_r   = self._pred(prediction, current_price)
            fvg_s,    fvg_r    = self._fvg(candles, current_price)
            ob_s,     ob_r     = self._ob(candles, current_price)
            fvg15_s,  fvg15_r  = self._fvg_15m(smc_candles, current_price)
            str_s,    str_r    = self._struct(smc_candles)

            total = (rsi_s + macd_s + bb_s + vol_s + pred_s
                     + fvg_s + ob_s + fvg15_s + str_s)

            factors = [
                {"name": "RSI",        "score": round(rsi_s,   1), "max": W_RSI,     "reason": rsi_r},
                {"name": "MACD",       "score": round(macd_s,  1), "max": W_MACD,    "reason": macd_r},
                {"name": "Bollinger",  "score": round(bb_s,    1), "max": W_BB,      "reason": bb_r},
                {"name": "Volatility", "score": round(vol_s,   1), "max": W_VOL,     "reason": vol_r},
                {"name": "Direction",  "score": round(pred_s,  1), "max": W_PREDICT, "reason": pred_r},
                {"name": "FVG",        "score": round(fvg_s,   1), "max": W_FVG,     "reason": fvg_r},
                {"name": "OrderBlock", "score": round(ob_s,    1), "max": W_OB,      "reason": ob_r},
                {"name": "FVG 15m",    "score": round(fvg15_s, 1), "max": W_FVG_15M, "reason": fvg15_r},
                {"name": "Structure",  "score": round(str_s,   1), "max": W_STRUCT,  "reason": str_r},
            ]

            # 동적 손절가 계산 (15분봉 FVG 하단 기반)
            stop_price = smc.structural_stop(smc_candles, float(current_price))

            if total >= BUY_THRESHOLD:
                signal  = "buy"
                summary = f"매수 시그널 (스코어 {total:+.0f}/100)"
            elif total <= SELL_THRESHOLD:
                signal  = "sell"
                summary = f"매도 시그널 (스코어 {total:+.0f}/100)"
            else:
                signal  = "hold"
                summary = f"관망 (스코어 {total:+.0f}/100)"

            result = {
                "signal":     signal,
                "score":      round(total, 1),
                "factors":    factors,
                "summary":    summary,
                "price":      current_price,
                "stop_price": stop_price,
            }

            if prediction is None:
                _cache[code] = (time.time() + _TTL, result)
            return result

        except Exception as e:
            logger.error(f"Strategy evaluate failed for {code}: {e}")
            return {
                "signal":     "hold",
                "score":      0,
                "factors":    [],
                "summary":    f"평가 실패: {e}",
                "price":      0,
                "stop_price": None,
            }

    # 동적 손절 — FVG 구조적 손절가 우선, 폴백으로 고정 %
    async def stop_loss(
        self, code: str, avg_price: int,
        structural_price: float | None = None,
        fallback_pct: float = -3.0,
    ) -> tuple[bool, float]:
        try:
            current = await kis.price_raw(code)
            pnl = (current - avg_price) / avg_price * 100

            # 구조적 손절가가 있으면 그 가격 하회 시 손절
            if structural_price and current < structural_price:
                return True, pnl

            # 폴백: 고정 % 손절
            return pnl <= fallback_pct, pnl
        except Exception:
            return False, 0.0


# 모듈 레벨 인스턴스
scorer = Scorer()

# 하위 호환
evaluate  = scorer.evaluate
stop_loss = scorer.stop_loss
