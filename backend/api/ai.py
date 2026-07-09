# AI 분석 API 라우터
from fastapi import APIRouter, HTTPException, Path, Request
from service.ai.pipeline import pipeline
from service.market.indicators import summary
from service.kis import kis
from api.limiter import limiter

router = APIRouter(prefix="/api/ai")

# 종목코드 경로 파라미터 (6자리 숫자만 허용)
_CODE = Path(pattern=r"^\d{6}$")


# 기술지표 + 뉴스 종합 AI 시그널 분석
@router.get("/signal/{code}")
@limiter.limit("20/minute")
async def signal(request: Request, code: str = _CODE):
    result = await pipeline.analyze(code)
    if not result:
        raise HTTPException(502, "AI 분석 실패")
    return result


# 종목별 뉴스 감성 분석 (긍정/중립/부정)
@router.get("/news/{code}")
@limiter.limit("20/minute")
async def news(request: Request, code: str = _CODE):
    result = await pipeline.sentiment(code)
    if not result:
        raise HTTPException(502, "뉴스 감성 분석 실패")
    return result


# 당일 마켓 리포트 생성 (Gemini 필요)
@router.get("/report")
@limiter.limit("6/minute")
async def report(request: Request):
    if not pipeline.enabled:
        raise HTTPException(503, "AI 기능이 비활성화 상태입니다 (GEMINI_API_KEY 미설정)")
    result = await pipeline.report()
    if not result:
        raise HTTPException(502, "리포트 생성 실패")
    return {"report": result}


# RSI / MACD / 볼린저밴드 기술 지표 요약
@router.get("/indicators/{code}")
@limiter.limit("20/minute")
async def ind(request: Request, code: str = _CODE):
    try:
        candles = await kis.daily(code)
        return summary(candles)
    except Exception:
        raise HTTPException(502, "기술 지표 계산 실패")
