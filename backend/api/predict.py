# AI 예측 API 라우터
from fastapi import APIRouter, HTTPException
from service.kis import NAMES
from service.predict import predict_stock

router = APIRouter(prefix="/api/predict")


@router.get("/{code}")
async def predict(code: str):
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
    except Exception as e:
        raise HTTPException(502, f"예측 실패: {e}")
