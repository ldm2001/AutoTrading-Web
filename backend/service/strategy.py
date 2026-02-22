# 멀티팩터 매매 전략 스코어러
import asyncio
import logging
import time

from service.kis import kis
from service import indicators

logger = logging.getLogger(__name__)

# 팩터별 최대 점수 (합계 100점)
W_RSI     = 25
W_MACD    = 25
W_BB      = 20
W_VOL     = 15
W_PREDICT = 15

# 매수/매도 진입 임계값
BUY_THRESHOLD  = 55
SELL_THRESHOLD = -40

# 평가 캐시 (prediction=None 일 때만 저장, 2분 TTL)
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
        # 골든크로스 → 매수
        if hist > 0 and macd_val > signal_val:
            strength = min(abs(hist) / max(abs(signal_val), 1) * 10, 1.0)
            return W_MACD * strength, f"MACD 골든크로스 (hist={hist:+.1f})"
        # 데드크로스 → 매도
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
        # 하단 이탈 → 매수 (반등 기대)
        if price <= lower:
            return W_BB, f"볼린저 하단 이탈 ({price:,.0f} ≤ {lower:,.0f})"
        # 하단 근접
        if price < lower + band_width * 0.2:
            return W_BB * 0.6, "볼린저 하단 근접"
        # 상단 이탈 → 매도 (과열)
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

    # Transformer 예측 점수화 (-15 ~ +15)
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
        if change_pct > 3 and uptrend_cnt >= 3:
            return W_PREDICT, f"AI 예측 +{change_pct:.1f}% (5일 상승 추세)"
        if change_pct > 1:
            return W_PREDICT * 0.6, f"AI 예측 +{change_pct:.1f}% (소폭 상승)"
        if change_pct < -3 and uptrend_cnt <= 1:
            return -W_PREDICT, f"AI 예측 {change_pct:.1f}% (5일 하락 추세)"
        if change_pct < -1:
            return -W_PREDICT * 0.6, f"AI 예측 {change_pct:.1f}% (소폭 하락)"
        return 0, f"AI 예측 {change_pct:+.1f}% (중립)"

    # 종목 종합 평가 (멀티팩터 스코어링)
    async def evaluate(self, code: str, prediction: dict | None = None) -> dict:
        # prediction 없을 때만 캐시 활용
        if prediction is None:
            cached = _cache.get(code)
            if cached and time.time() < cached[0]:
                return cached[1]
        try:
            # daily + price 병렬 요청
            candles, price_info = await asyncio.gather(
                kis.daily(code),
                kis.price(code),
            )
            current_price = price_info["price"]
            ind = indicators.summary(candles)

            rsi_s,  rsi_r  = self._rsi(ind["rsi"])
            macd_s, macd_r = self._macd(ind["macd"])
            bb_s,   bb_r   = self._bb(ind["bollinger"])
            vol_s,  vol_r  = self._vol(candles, current_price)
            pred_s, pred_r = self._pred(prediction, current_price)

            total = rsi_s + macd_s + bb_s + vol_s + pred_s

            factors = [
                {"name": "RSI",        "score": round(rsi_s,  1), "max": W_RSI,     "reason": rsi_r},
                {"name": "MACD",       "score": round(macd_s, 1), "max": W_MACD,    "reason": macd_r},
                {"name": "Bollinger",  "score": round(bb_s,   1), "max": W_BB,      "reason": bb_r},
                {"name": "Volatility", "score": round(vol_s,  1), "max": W_VOL,     "reason": vol_r},
                {"name": "AI Predict", "score": round(pred_s, 1), "max": W_PREDICT, "reason": pred_r},
            ]

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
                "signal":  signal,
                "score":   round(total, 1),
                "factors": factors,
                "summary": summary,
                "price":   current_price,
            }

            if prediction is None:
                _cache[code] = (time.time() + _TTL, result)
            return result

        except Exception as e:
            logger.error(f"Strategy evaluate failed for {code}: {e}")
            return {
                "signal":  "hold",
                "score":   0,
                "factors": [],
                "summary": f"평가 실패: {e}",
                "price":   0,
            }

    # 손절 판단 (기본 -3%)
    async def stop_loss(
        self, code: str, avg_price: int, pct: float = -3.0
    ) -> tuple[bool, float]:
        try:
            current = await kis.price_raw(code)
            pnl     = (current - avg_price) / avg_price * 100
            return pnl <= pct, pnl
        except Exception:
            return False, 0.0


# 모듈 레벨 인스턴스
scorer = Scorer()

# 하위 호환 별칭
evaluate         = scorer.evaluate
should_stop_loss = scorer.stop_loss
