# 기술 지표 모듈 (RSI, MACD, 볼린저밴드)
import math


class Indicators:

    # 종가 배열 추출
    def _closes(self, candles: list[dict]) -> list[float]:
        return [float(c["close"]) for c in candles]

    # EMA (지수이동평균) 계산
    def _ema(self, values: list[float], period: int) -> list[float]:
        if not values:
            return []
        k = 2 / (period + 1)
        result = [values[0]]
        for v in values[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    # RSI 계산 (기본 14일)
    def rsi(self, candles: list[dict], period: int = 14) -> float | None:
        closes = self._closes(candles)
        if len(closes) < period + 1:
            return None
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
        for i in range(period + 1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                avg_gain = (avg_gain * (period - 1) + diff) / period
                avg_loss = (avg_loss * (period - 1)) / period
            else:
                avg_gain = (avg_gain * (period - 1)) / period
                avg_loss = (avg_loss * (period - 1) - diff) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 2)

    # MACD 계산 (기본 12/26/9)
    def macd(
        self,
        candles: list[dict],
        fast: int = 12,
        slow: int = 26,
        signal_period: int = 9,
    ) -> dict | None:
        closes = self._closes(candles)
        if len(closes) < slow + signal_period:
            return None
        ema_fast    = self._ema(closes, fast)
        ema_slow    = self._ema(closes, slow)
        macd_line   = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = self._ema(macd_line[slow - 1:], signal_period)
        m = macd_line[-1]
        s = signal_line[-1] if signal_line else 0
        return {
            "macd":      round(m, 2),
            "signal":    round(s, 2),
            "histogram": round(m - s, 2),
        }

    # 볼린저밴드 계산 (기본 20일 2σ)
    def bollinger(
        self,
        candles: list[dict],
        period: int = 20,
        std_dev: float = 2.0,
    ) -> dict | None:
        closes = self._closes(candles)
        if len(closes) < period:
            return None
        window   = closes[-period:]
        middle   = sum(window) / period
        variance = sum((x - middle) ** 2 for x in window) / period
        sd       = math.sqrt(variance)
        return {
            "upper":         round(middle + std_dev * sd, 2),
            "middle":        round(middle, 2),
            "lower":         round(middle - std_dev * sd, 2),
            "current_price": closes[-1],
        }

    # 전체 기술 지표 요약
    def summary(self, candles: list[dict]) -> dict:
        return {
            "rsi":       self.rsi(candles),
            "macd":      self.macd(candles),
            "bollinger": self.bollinger(candles),
            "price":     candles[-1]["close"] if candles else None,
            "volume":    candles[-1]["volume"] if candles else None,
        }


# 모듈 레벨 인스턴스 (기존 코드와 호환)
_ind     = Indicators()
rsi      = _ind.rsi
macd     = _ind.macd
bollinger = _ind.bollinger
summary  = _ind.summary
