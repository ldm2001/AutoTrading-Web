# AI 예측 API 라우터
from fastapi import APIRouter, HTTPException, Path, Request
from service.kis import NAMES
from service.ai.predict import forecast
from api.limiter import limiter

router = APIRouter(prefix="/api/predict")


# Transformer 기반 5일 주가 예측 반환 — 공용 모델이 없으면 종목별 즉석 학습이라 rate-limit 적용
@router.get("/{code}")
@limiter.limit("6/minute")
async def outlook(request: Request, code: str = Path(pattern=r"^\d{1,6}$")):
    code = code.zfill(6)
    name = NAMES.get(code, code)
    try:
        result = await forecast(code)
        return {
            "code":        code,
            "name":        name,
            "predictions": result["predictions"],
            "metrics":     result["metrics"],
            # 예측 기준 — 학습에 쓴 확정 일봉의 마지막 종가와 날짜
            "base_close":  result.get("base_close"),
            "base_date":   result.get("base_date"),
            # 예측 출처 — pooled(공용 모델 추론) / per_symbol(종목별 즉석 학습, 화면 표시용), validated는 메타 값이 true일 때만 true
            "source":      result.get("source", "per_symbol"),
            "validated":   result.get("validated") is True,
            "status":      "success",
        }
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception:
        raise HTTPException(502, "예측 처리 실패")
