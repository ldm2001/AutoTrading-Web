# 기술 지표 모듈 (RSI, MACD, 볼린저밴드) — numpy 벡터 연산
import numpy as np


class Indicators:

    # 종가 배열 → numpy float64
    def _arr(self, candles: list[dict]) -> np.ndarray:
        return np.array([c["close"] for c in candles], dtype=np.float64)

    # EMA — 초기값 첫 원소, 이후 지수평활
    def _ema(self, arr: np.ndarray, period: int) -> np.ndarray:
        k = 2.0 / (period + 1)
        out = np.empty(len(arr))
        out[0] = arr[0]
        for i in range(1, len(arr)):
            out[i] = arr[i] * k + out[i - 1] * (1 - k)
        return out

    # RSI 계산 (기본 14일) — Wilder smoothing
    def rsi(self, candles: list[dict], period: int = 14) -> float | None:
        closes = self._arr(candles)
        if len(closes) < period + 1:
            return None
        delta  = np.diff(closes)
        gains  = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)
        avg_g  = gains[:period].mean()
        avg_l  = losses[:period].mean()
        for i in range(period, len(delta)):
            avg_g = (avg_g * (period - 1) + gains[i])  / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            return 100.0
        return round(100 - 100 / (1 + avg_g / avg_l), 2)

    # MACD 계산 (기본 12/26/9)
    def macd(
        self,
        candles: list[dict],
        fast: int = 12,
        slow: int = 26,
        signal_period: int = 9,
    ) -> dict | None:
        closes = self._arr(candles)
        if len(closes) < slow + signal_period:
            return None
        ema_f = self._ema(closes, fast)
        ema_s = self._ema(closes, slow)
        line  = ema_f - ema_s
        sig   = self._ema(line[slow - 1:], signal_period)
        m, s  = float(line[-1]), float(sig[-1])
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
        closes = self._arr(candles)
        if len(closes) < period:
            return None
        window = closes[-period:]
        mid    = float(window.mean())
        sd     = float(window.std(ddof=0))
        return {
            "upper":         round(mid + std_dev * sd, 2),
            "middle":        round(mid, 2),
            "lower":         round(mid - std_dev * sd, 2),
            "current_price": float(closes[-1]),
        }

    # ATR (Average True Range) — 변동성 지표
    def atr(self, candles: list[dict], period: int = 14) -> float | None:
        if len(candles) < period + 1:
            return None
        highs  = np.array([c["high"]  for c in candles], dtype=np.float64)
        lows   = np.array([c["low"]   for c in candles], dtype=np.float64)
        closes = np.array([c["close"] for c in candles], dtype=np.float64)
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            ),
        )
        atr_val = tr[-period:].mean()
        return round(float(atr_val), 2)

    # 변동성 종합 분석
    def volatility(self, candles: list[dict]) -> dict:
        closes = self._arr(candles)
        result: dict = {"atr": self.atr(candles), "atr_pct": None, "bb_width": None, "daily_range_pct": None, "volatility_grade": "N/A"}
        if len(closes) < 2:
            return result
        price = float(closes[-1])
        if result["atr"] and price > 0:
            result["atr_pct"] = round(result["atr"] / price * 100, 2)
        bb = self.bollinger(candles)
        if bb and price > 0:
            result["bb_width"] = round((bb["upper"] - bb["lower"]) / bb["middle"] * 100, 2)
        highs = np.array([c["high"] for c in candles[-20:]], dtype=np.float64)
        lows  = np.array([c["low"]  for c in candles[-20:]], dtype=np.float64)
        avg_range = float(((highs - lows) / closes[-20:]).mean() * 100)
        result["daily_range_pct"] = round(avg_range, 2)
        atr_pct = result["atr_pct"] or 0
        if atr_pct >= 4:
            result["volatility_grade"] = "매우높음"
        elif atr_pct >= 2.5:
            result["volatility_grade"] = "높음"
        elif atr_pct >= 1.5:
            result["volatility_grade"] = "보통"
        else:
            result["volatility_grade"] = "낮음"
        return result

    # 전체 기술 지표 요약
    def summary(self, candles: list[dict]) -> dict:
        return {
            "rsi":       self.rsi(candles),
            "macd":      self.macd(candles),
            "bollinger": self.bollinger(candles),
            "price":     candles[-1]["close"] if candles else None,
            "volume":    candles[-1]["volume"] if candles else None,
        }


# 모듈 레벨 인스턴스
_ind      = Indicators()
rsi        = _ind.rsi
macd       = _ind.macd
bollinger  = _ind.bollinger
atr        = _ind.atr
volatility = _ind.volatility
summary    = _ind.summary
