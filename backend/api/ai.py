# AI 분석 API 라우터
from fastapi import APIRouter, HTTPException
from service.ai_pipeline import pipeline
from service.kis import kis
from service import indicators

router = APIRouter(prefix="/api/ai")


# 기술지표 + 뉴스 종합 AI 시그널 분석
@router.get("/signal/{code}")
async def signal(code: str):
    result = await pipeline.analyze(code)
    if not result:
        raise HTTPException(502, "AI 분석 실패")
    return result


# 종목별 뉴스 감성 분석 (긍정/중립/부정)
@router.get("/news/{code}")
async def news(code: str):
    result = await pipeline.sentiment(code)
    if not result:
        raise HTTPException(502, "뉴스 감성 분석 실패")
    return result


# 당일 마켓 리포트 생성 (Gemini 필요)
@router.get("/report")
async def report():
    if not pipeline.enabled:
        raise HTTPException(503, "AI 기능이 비활성화 상태입니다 (GEMINI_API_KEY 미설정)")
    result = await pipeline.report()
    if not result:
        raise HTTPException(502, "리포트 생성 실패")
    return {"report": result}


# RSI / MACD / 볼린저밴드 기술 지표 요약
@router.get("/indicators/{code}")
async def ind(code: str):
    try:
        candles = await kis.daily(code)
        return indicators.summary(candles)
    except Exception as e:
        raise HTTPException(502, f"기술 지표 계산 실패: {e}")
