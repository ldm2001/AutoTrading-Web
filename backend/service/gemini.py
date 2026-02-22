# 제미나이 API 클라이언트
import json
import logging
import time
from typing import Any
import google.generativeai as genai
from config import settings

logger = logging.getLogger(__name__)

# 응답 캐시 (key → (만료시각, 값))
_cache: dict[str, tuple[float, Any]] = {}

# 캐시 조회 — TTL 만료 시 None 반환
def _cached(key: str, ttl: float) -> Any | None:
    entry = _cache.get(key)
    if entry and time.time() < entry[0]:
        return entry[1]
    return None

# 캐시 저장
def _set_cache(key: str, value: Any, ttl: float) -> None:
    _cache[key] = (time.time() + ttl, value)

class GeminiClient:

    # API 키 유무에 따라 활성/비활성 초기화
    def __init__(self) -> None:
        self.enabled = bool(settings.gemini_api_key)
        if self.enabled:
            genai.configure(api_key=settings.gemini_api_key)
            self._model = genai.GenerativeModel(settings.gemini_model)
            logger.info(f"Gemini API initialized (model: {settings.gemini_model})")
        else:
            self._model = None
            logger.info("Gemini API key not set - AI features disabled")

    # Gemini API 호출 — 텍스트 응답 반환
    async def _generate(self, prompt: str) -> str | None:
        if not self.enabled:
            return None
        try:
            response = await self._model.generate_content_async(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return None

    # Gemini API 호출 — JSON 파싱 후 dict 반환
    async def _generate_json(self, prompt: str) -> dict | None:
        text = await self._generate(prompt)
        if not text:
            return None
        try:
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]
            return json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            logger.error(f"Gemini JSON parse failed: {text[:200]}")
            return None

    # 기술지표 + 뉴스 기반 매매 시그널 분석 (1분 캐시)
    async def analyze_signal(
        self, indicators: dict, news: list[dict], stock_info: dict
    ) -> dict | None:
        cache_key = f"signal:{stock_info.get('code', '')}"
        cached = _cached(cache_key, 60)
        if cached:
            return cached

        prompt = f"""당신은 한국 주식 시장 전문 애널리스트입니다.
아래 데이터를 분석하여 매매 시그널을 JSON으로 반환하세요.

## 종목 정보
- 종목: {stock_info.get('name', '')} ({stock_info.get('code', '')})
- 현재가: {stock_info.get('price', 0):,}원
- 전일대비: {stock_info.get('change_percent', 0)}%
- 거래량: {stock_info.get('volume', 0):,}

## 기술 지표
- RSI(14): {indicators.get('rsi', 'N/A')}
- MACD: {json.dumps(indicators.get('macd', {}), ensure_ascii=False)}
- 볼린저밴드: {json.dumps(indicators.get('bollinger', {}), ensure_ascii=False)}

## 최근 뉴스
{chr(10).join(f"- {n['title']}" for n in news[:5]) if news else "- 관련 뉴스 없음"}

## 응답 형식 (JSON만 반환)
```json
{{
  "signal": "buy" | "hold" | "sell",
  "confidence": 0-100,
  "reasons": ["이유1", "이유2", "이유3"]
}}
```"""

        result = await self._generate_json(prompt)
        if result:
            _set_cache(cache_key, result, 60)
        return result

    # 뉴스 감성 분석 — 긍정/중립/부정 분류 + 전체 점수 반환 (5분 캐시)
    async def analyze_sentiment(self, news: list[dict], stock_name: str) -> dict | None:
        cache_key = f"sentiment:{stock_name}"
        cached = _cached(cache_key, 300)
        if cached:
            return cached

        if not news:
            return {"score": 0, "summary": "관련 뉴스가 없습니다.", "articles": []}

        articles_text = "\n".join(
            f"{i+1}. {n['title']} - {n.get('summary', '')}"
            for i, n in enumerate(news[:10])
        )

        prompt = f"""당신은 한국 주식 시장 뉴스 감성 분석 전문가입니다.
아래 {stock_name} 관련 뉴스를 분석하여 감성 점수를 JSON으로 반환하세요.

## 뉴스 목록
{articles_text}

## 응답 형식 (JSON만 반환)
```json
{{
  "score": -100에서 100 사이 정수 (부정적 ~ 긍정적),
  "summary": "전체 감성 요약 (1-2문장)",
  "articles": [
    {{"title": "뉴스 제목", "sentiment": "positive|negative|neutral", "score": -100~100}}
  ]
}}
```"""

        result = await self._generate_json(prompt)
        if result:
            _set_cache(cache_key, result, 300)
        return result

    # 일일 마켓 리포트 마크다운 생성 (1시간 캐시)
    async def generate_report(
        self, trades: list[dict], portfolio: dict, market: list[dict],
        *, today_str: str = "", market_open: bool = True
    ) -> str | None:
        cache_key = "daily_report"
        cached = _cached(cache_key, 3600)
        if cached:
            return cached

        market_text = "\n".join(
            f"- {m.get('name', '')}: {m.get('value', 0):,.2f} ({m.get('change_percent', 0):+.2f}%)"
            for m in market
        ) if market else "- 시장 데이터 없음"

        trade_text = "\n".join(
            f"- [{t.get('type', '')}] {t.get('name', '')} {t.get('qty', 0)}주 "
            f"({'성공' if t.get('success') else '실패'})"
            for t in trades
        ) if trades else "- 오늘 매매 내역 없음"

        items = portfolio.get("items", [])
        portfolio_text = "\n".join(
            f"- {p.get('name', '')}: {p.get('qty', 0)}주, "
            f"수익률 {p.get('profit_loss_percent', 0):+.2f}%"
            for p in items
        ) if items else "- 보유 종목 없음"

        market_status = "정상 개장일" if market_open else "휴장일 (공휴일/주말)"

        prompt = f"""당신은 한국 주식 시장 일일 리포트를 작성하는 애널리스트입니다.
아래 데이터를 바탕으로 오늘의 마켓 리포트를 마크다운으로 작성하세요.

## 오늘 날짜 및 장 상태
- 날짜: {today_str}
- 장 상태: {market_status}
{"- 주의: 오늘은 휴장일입니다. 시장 지수는 직전 거래일 데이터이며, 오늘 실제 거래는 없었습니다." if not market_open else ""}

## 시장 지수{" (직전 거래일 기준)" if not market_open else ""}
{market_text}

## 오늘 매매 내역
{trade_text}

## 보유 포트폴리오
{portfolio_text}
- 총 평가금액: {portfolio.get('total_eval', 0):,}원
- 총 손익: {portfolio.get('total_profit_loss', 0):,}원
- 예수금: {portfolio.get('cash_balance', 0):,}원

## 작성 규칙
- 마크다운 형식으로 작성
{"- 휴장일이므로 실제 거래가 없었음을 명시하고, 직전 거래일 데이터 기반으로 현황 정리" if not market_open else "- 시장 동향 요약 (2-3문장)"}
{"- 다음 개장일 전략 제안" if not market_open else "- 오늘 매매 분석"}
- 포트폴리오 현황 평가
- {"다음 개장일" if not market_open else "내일"} 전략 제안 (2-3줄)
- 한국어로 작성"""

        result = await self._generate(prompt)
        if result:
            _set_cache(cache_key, result, 3600)
        return result

gemini = GeminiClient()
