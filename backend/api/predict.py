# AI 예측 API 라우터
from fastapi import APIRouter, HTTPException, Path, Request
from service.kis import NAMES
from service.ai.predict import predict_stock
from api.limiter import limiter

router = APIRouter(prefix="/api/predict")


# Transformer 기반 5일 주가 예측 반환 — 종목별 즉석 학습이라 rate-limit 적용
@router.get("/{code}")
@limiter.limit("6/minute")
async def predict(request: Request, code: str = Path(pattern=r"^\d{1,6}$")):
    code = code.zfill(6)
    name = NAMES.get(code, code)
    try:
        result = await predict_stock(code)
        return {
            "code":        code,
            "name":        name,
            "predictions": result["predictions"],
            "metrics":     result["metrics"],
            "status":      "success",
        }
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception:
        raise HTTPException(502, "예측 처리 실패")
