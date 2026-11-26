# 멀티팩터 매매 전략 스코어러
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from config import settings
from service.ai.spec import VERSION
from service.market import indicators
from service.market import smc
from service.trading.ports import Quotes
from service.infra.ttl_cache import TTLCache

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
_cache = TTLCache()
_TTL   = 120

# 팩터 계산 입력 묶음
@dataclass
class FactorInput:
    ind: dict
    candles: list[dict]
    price: int
    prediction: dict | None
    candles_15m: list[dict] | None
    fast: bool

# 15분봉 FVG 팩터 — fast/데이터부족 시 특수 사유
def fvg15factor(s, fi: FactorInput) -> tuple[float, str]:
    if fi.fast:
        return 0.0, "1단계 스크리닝에서 제외"
    if fi.candles_15m:
        return s.fvg15(fi.candles_15m, fi.price)
    return 0.0, "15분봉 데이터 부족"

# BOS/CHoCH 구조 팩터 — fast/데이터부족 시 특수 사유
def structfactor(s, fi: FactorInput) -> tuple[float, str]:
    if fi.fast:
        return 0.0, "1단계 스크리닝에서 제외"
    if fi.candles_15m:
        return s.struct(fi.candles_15m)
    return 0.0, "15분봉 데이터 부족"

# 매매 점수의 문 — Direction은 여기가 열릴 때("")만 0이 아님, 닫히면 0점 사유 (판정은 이 함수 한 곳, Scorer.pred만 부름)
# 열림 = 플래그 True + 예측 출처 pooled + 예측 validated True + 예측 사양 = 지금 VERSION + 지금 읽힌 공용 모델 메타도 validated True·같은 사양
# 공용 모델 확인은 앞 조건을 다 지난 경우만 (기본 경로에서 가중치 파일·torch를 읽지 않음), 확인 실패는 경고 + 닫힘
def predgate(prediction) -> str:
    if not settings.use_prediction:
        return "예측 연동 꺼짐"
    if not isinstance(prediction, dict):
        return "예측 형식 오류"
    if prediction.get("source") != "pooled":
        return "공용 모델 예측 아님"
    if prediction.get("validated") is not True:
        return "검증 전 예측"
    if prediction.get("spec") != VERSION:
        return "예측 사양 불일치"
    try:
        # 지연 임포트 (torch) — 서비스 프로세스에서는 api/stock이 이미 읽어 둠
        from service.ai.predict import predictor
        pool = predictor.bundle()
    except Exception as e:
        logger.warning(f"공용 모델 확인 실패 — Direction 0점: {e}")
        return "공용 모델 확인 실패"
    if not isinstance(pool, dict):
        return "공용 모델 없음"
    meta = pool.get("meta")
    if not isinstance(meta, dict) or meta.get("validated") is not True:
        return "공용 모델 검증 전"
    if meta.get("version") != VERSION:
        return "공용 모델 사양 불일치"
    return ""

# 팩터 레지스트리 — (이름, 최대점수, 계산함수). 신규 팩터는 여기에 추가만 (OCP)
_FACTORS = [
    ("RSI",        W_RSI,     lambda s, fi: s.rsi(fi.ind["rsi"])),
    ("MACD",       W_MACD,    lambda s, fi: s.macd(fi.ind["macd"])),
    ("Bollinger",  W_BB,      lambda s, fi: s.bb(fi.ind["bollinger"])),
    ("Volatility", W_VOL,     lambda s, fi: s.vol(fi.candles, fi.price)),
    ("Direction",  W_PREDICT, lambda s, fi: s.pred(fi.prediction, fi.price)),
    ("FVG",        W_FVG,     lambda s, fi: s.fvg(fi.candles, fi.price)),
    ("OrderBlock", W_OB,      lambda s, fi: s.ob(fi.candles, fi.price)),
    ("FVG 15m",    W_FVG_15M, fvg15factor),
    ("Structure",  W_STRUCT,  structfactor),
]

# 9팩터 스코어링 엔진
class Scorer:
    # broker 주입 (Quotes 포트) — 합성 루트에서 bind
    def __init__(self, broker: Quotes | None = None) -> None:
        self._broker = broker

    # 합성 루트에서 broker 주입
    def binding(self, broker: Quotes) -> None:
        self._broker = broker

    # broker 반환 (미주입 시 fail-fast — 서비스 로케이터 제거)
    @property
    def broker(self) -> Quotes:
        if self._broker is None:
            raise RuntimeError("Scorer.broker 미주입 — 합성 루트에서 bind 필요")
        return self._broker

    # 캐시 키 생성 (prediction 있으면 캐싱 안 함)
    def ckey(self, code: str, *, fast: bool, prediction: dict | None) -> str | None:
        if prediction is not None:
            return None
        mode = "fast" if fast else "full"
        return f"{mode}:{code}"

    # RSI 점수화 (-25 ~ +25)
    def rsi(self, val: float | None) -> tuple[float, str]:
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
    def macd(self, data: dict | None) -> tuple[float, str]:
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
    def bb(self, data: dict | None) -> tuple[float, str]:
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
    def vol(self, candles: list[dict], price: int) -> tuple[float, str]:
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
    def fvg(self, candles: list[dict], price: int) -> tuple[float, str]:
        try:
            s, r = smc.fvg(candles, float(price))
            return round(s * (W_FVG / 8), 1), r
        except Exception:
            return 0.0, "FVG 계산 오류"

    # OB 지지/저항 점수화 (-7 ~ +7)
    def ob(self, candles: list[dict], price: int) -> tuple[float, str]:
        try:
            s, r = smc.ob(candles, float(price))
            return round(s * (W_OB / 7), 1), r
        except Exception:
            return 0.0, "OB 계산 오류"

    # 15분봉 FVG 점수화 (-15 ~ +15) — 실제 진입 트리거
    def fvg15(self, candles_15m: list[dict], price: int) -> tuple[float, str]:
        try:
            s, r, _ = smc.fvgin(candles_15m, float(price))
            return round(s * (W_FVG_15M / 10), 1), r
        except Exception:
            return 0.0, "15m FVG 계산 오류"

    # BOS/CHoCH 구조 점수화 (-8 ~ +8)
    def struct(self, candles: list[dict]) -> tuple[float, str]:
        try:
            s, r = smc.struct(candles)
            return round(s * (W_STRUCT / 5), 1), r
        except Exception:
            return 0.0, "구조 분석 오류"

    # Transformer 예측 → 방향성 필터 (-10 ~ +10) — 봇·추천·백테스트가 모두 여기를 지남, 문(predgate)이 열릴 때만 규칙대로 채점
    def pred(self, prediction: dict | None, price: int) -> tuple[float, str]:
        if prediction is None:
            return 0, "예측 데이터 없음"
        shut = predgate(prediction)
        if shut:
            return 0, shut
        return self.labscore(prediction, price)

    # 문 없는 Direction 규칙 — 서비스 코드는 pred(문 통과 뒤)로만 부름, 직접 부르는 것은 오프라인 하네스뿐 (tests/test_gate.py가 고정)
    # 기준은 학습에 쓴 확정 일봉의 마지막 종가(base_close) — price는 호출 형태 유지용으로만 받음
    def labscore(self, prediction: dict | None, price: int | None = None) -> tuple[float, str]:
        if prediction is None:
            return 0, "예측 데이터 없음"
        preds = prediction.get("predictions", [])
        if not preds:
            return 0, "예측 결과 없음"
        base = prediction.get("base_close")
        if not base:
            return 0, "예측 기준가 없음"
        last_close  = preds[-1]["close"]
        change_pct  = (last_close - base) / base * 100
        uptrend_cnt = 0
        prev        = base
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
    async def verdict(self, code: str, prediction: dict | None = None, fast: bool = False) -> dict:
        cache_key = self.ckey(code, fast=fast, prediction=prediction)
        if cache_key is not None:
            cached = _cache.lookup(cache_key)
            if cached is not None:
                return cached
        try:
            b = self.broker
            if fast:
                candles, price_info = await asyncio.gather(
                    b.daily(code), b.price(code),
                )
                candles_15m = None
            else:
                candles, price_info, candles_15m = await asyncio.gather(
                    b.daily(code), b.price(code), b.c15(code),
                )
            current_price = price_info["price"]
            ind = indicators.summary(candles)

            fi = FactorInput(
                ind=ind,
                candles=candles,
                price=current_price,
                prediction=prediction,
                candles_15m=candles_15m,
                fast=fast,
            )
            total = 0
            factors = []
            for name, maxw, fn in _FACTORS:
                score, reason = fn(self, fi)
                total += score
                factors.append({"name": name, "score": round(score, 1), "max": maxw, "reason": reason})

            # 동적 손절가는 full 평가 + 실제 15분봉 데이터가 있을 때만 계산
            stop_price = (
                smc.slprice(candles_15m, float(current_price))
                if candles_15m
                else None
            )

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

            if cache_key is not None:
                _cache.memo(cache_key, result, _TTL)
            return result

        except Exception as e:
            logger.error(f"Strategy evaluate failed for {code}: {e}")
            return {
                "signal":     "hold",
                "score":      0,
                "factors":    [],
                "summary":    "평가 실패 (서버 로그 참조)",
                "price":      0,
                "stop_price": None,
            }

# 모듈 레벨 싱글턴 인스턴스 (broker는 합성 루트 main.py에서 bind)
scorer = Scorer()

# 하위 호환
verdict = scorer.verdict
