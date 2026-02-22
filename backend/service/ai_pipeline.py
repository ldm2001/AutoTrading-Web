# AI 분석 파이프라인 오케스트레이터
import asyncio
import logging
import time

from datetime import date
from service.kis import kis, NAMES, ALL_STOCKS
from service.gemini import gemini
from service import news
from service import indicators

logger = logging.getLogger(__name__)

# analyze 결과 캐시 (3분 TTL)
_analyze_cache: dict[str, tuple[float, dict]] = {}
_ANALYZE_TTL = 180

# sentiment 결과 캐시 (5분 TTL)
_sentiment_cache: dict[str, tuple[float, dict]] = {}
_SENTIMENT_TTL = 300

# 한국 공휴일 2026 (음력 변동분 수동 갱신)
_KR_HOLIDAYS_2026 = {
    (1, 1), (1, 28), (1, 29), (1, 30),
    (3, 1), (5, 5), (5, 24),
    (6, 6), (8, 15),
    (9, 24), (9, 25), (9, 26),
    (10, 3), (10, 9), (12, 25),
}


# 장 개장 여부 확인 (주말/공휴일 제외)
def _market_open(d: date | None = None) -> bool:
    d = d or date.today()
    if d.weekday() >= 5:
        return False
    if (d.month, d.day) in _KR_HOLIDAYS_2026:
        return False
    return True


# 종목 코드 → 이름 조회
def _name(code: str) -> str:
    if ALL_STOCKS and code in ALL_STOCKS:
        return ALL_STOCKS[code]["name"]
    return NAMES.get(code, code)


class AIPipeline:

    @property
    def enabled(self) -> bool:
        return gemini.enabled

    # 종목 종합 분석 (기술 지표 + 뉴스 + AI 시그널)
    async def analyze(self, code: str) -> dict | None:
        cached = _analyze_cache.get(code)
        if cached and time.time() < cached[0]:
            return cached[1]
        try:
            stock_name = _name(code)
            # daily + news + price 병렬 요청
            candles, stock_news, stock_info = await asyncio.gather(
                kis.daily(code),
                news.fetch_news(code),
                kis.price(code),
            )
            ind = indicators.summary(candles)
            if gemini.enabled:
                ai_signal = await gemini.analyze_signal(ind, stock_news, stock_info)
            else:
                ai_signal = None
            result = {
                "code":       code,
                "name":       stock_name,
                "indicators": ind,
                "news_count": len(stock_news),
                **(ai_signal or {
                    "signal":     "hold",
                    "confidence": 0,
                    "reasons":    ["Gemini API 미설정" if not gemini.enabled else "AI 분석 실패"],
                }),
            }
            _analyze_cache[code] = (time.time() + _ANALYZE_TTL, result)
            return result
        except Exception as e:
            logger.error(f"AI analyze failed for {code}: {e}")
            return None

    # 뉴스 감성 분석
    async def sentiment(self, code: str) -> dict | None:
        cached = _sentiment_cache.get(code)
        if cached and time.time() < cached[0]:
            return cached[1]
        try:
            stock_name = _name(code)
            stock_news = await news.fetch_news(code, count=10)
            if gemini.enabled:
                gemini_result = await gemini.analyze_sentiment(stock_news, stock_name)
                if gemini_result:
                    result = {"code": code, "name": stock_name, **gemini_result}
                    _sentiment_cache[code] = (time.time() + _SENTIMENT_TTL, result)
                    return result
            # Gemini 미설정 → 뉴스 제목만 반환
            result = {
                "code":     code,
                "name":     stock_name,
                "score":    0,
                "summary":  "" if not gemini.enabled else "감성 분석 실패",
                "articles": [
                    {"title": n["title"], "sentiment": "neutral", "score": 0}
                    for n in stock_news
                ],
            }
            _sentiment_cache[code] = (time.time() + _SENTIMENT_TTL, result)
            return result
        except Exception as e:
            logger.error(f"Sentiment analysis failed for {code}: {e}")
            return None

    # 일일 마켓 리포트 생성
    async def report(self) -> str | None:
        if not gemini.enabled:
            return None
        try:
            from service.bot import bot
            status = bot.status()
            trades = status.get("today_trades", [])
            items, evaluation = await kis.holdings()
            cash   = await kis.cash()
            market = await kis.indices()
            portfolio = {
                "items":             list(items.values()),
                "total_eval":        int(evaluation.get("tot_evlu_amt", "0")),
                "total_profit_loss": int(evaluation.get("evlu_pfls_smtl_amt", "0")),
                "cash_balance":      cash,
            }
            today = date.today()
            return await gemini.generate_report(
                trades, portfolio, market,
                today_str=today.strftime("%Y-%m-%d (%A)"),
                market_open=_market_open(today),
            )
        except Exception as e:
            logger.error(f"Daily report generation failed: {e}")
            return None

    # 봇용 시그널 체크 ('buy'|'hold'|'sell')
    async def signal(self, code: str) -> str:
        if not gemini.enabled:
            return "hold"
        try:
            result = await self.analyze(code)
            if result:
                return result.get("signal", "hold")
        except Exception:
            pass
        return "hold"

    # 하위 호환 별칭
    daily_report = report
    check_signal = signal


pipeline = AIPipeline()
