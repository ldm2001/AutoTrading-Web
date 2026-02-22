# 종목 데이터 및 AI 추천 API 라우터
import asyncio
import time
import logging
from fastapi import APIRouter, HTTPException
from service import kis, CODES, NAMES, ALL_STOCKS, search
from service.strategy import evaluate
from service.predict import predict_stock

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stocks")

# AI 추천 캐시 (10분 TTL)
_recommend_cache: dict[str, tuple[float, list]] = {}
_RECOMMEND_TTL = 600

# ETF 제외 개별 종목 (추천 스캔 대상)
_SCAN_CODES = [
    code for code, name in NAMES.items()
    if not any(tag in name for tag in ["KODEX", "TIGER", "ETF"])
]


# 전체 상장사 목록 (코드+이름+마켓, 가격 미포함)
@router.get("")
async def stocks():
    source = ALL_STOCKS if ALL_STOCKS else {c: {"name": n, "market": "KOSPI"} for c, n in NAMES.items()}
    return [
        {"code": code, "name": info["name"], "market": info.get("market", "")}
        for code, info in source.items()
        if not any(tag in info["name"] for tag in ["KODEX", "TIGER", "ETF", "KBSTAR", "HANARO", "SOL ", "ARIRANG"])
    ]


@router.get("/index/all")
async def indices():
    try:
        return await kis.indices()
    except Exception as e:
        raise HTTPException(502, f"KIS API error: {e}")


@router.get("/search")
async def search_stocks(q: str):
    return search(q)


# 종목명/코드 검색 후 현재가 조회
@router.get("/search/{query}/price")
async def find(query: str):
    code = query
    if not query.isdigit():
        code = CODES.get(query, "")
        if not code:
            results = search(query)
            if results:
                code = results[0]["code"]
            else:
                raise HTTPException(404, "종목을 찾을 수 없습니다")
    try:
        return await kis.price(code)
    except Exception as e:
        raise HTTPException(502, f"Stock not found: {e}")


# AI 멀티팩터 + Transformer 2단계 추천
@router.get("/recommend")
async def recommend():
    cache_key = "recommend"
    cached    = _recommend_cache.get(cache_key)
    if cached and time.time() < cached[0]:
        return cached[1]

    sem = asyncio.Semaphore(5)

    # 1단계: 멀티팩터 스크리닝 (prediction 없이)
    async def _eval(code: str):
        async with sem:
            try:
                result = await evaluate(code)
                return {
                    "code":    code,
                    "name":    NAMES.get(code, code),
                    "signal":  result["signal"],
                    "score":   result["score"],
                    "price":   result["price"],
                    "summary": result["summary"],
                    "factors": result["factors"],
                }
            except Exception as e:
                logger.debug(f"Recommend eval skip {code}: {e}")
                return None

    tasks   = [_eval(code) for code in _SCAN_CODES]
    results = await asyncio.gather(*tasks)

    valid = [r for r in results if r is not None and r["score"] != 0]
    valid.sort(key=lambda x: x["score"], reverse=True)
    candidates = valid[:20]  # 상위 20개 후보 (최종 10개 선정)

    # 2단계: 상위 후보에 Transformer 예측 적용 후 재평가
    async def _eval_pred(item: dict):
        code = item["code"]
        try:
            pred  = await predict_stock(code)
            preds = pred.get("predictions", [])
            if not preds:
                item["prediction"] = None
                return item
            current_price = item["price"]
            predicted_5d  = preds[-1]["close"]
            change_pct    = round((predicted_5d - current_price) / current_price * 100, 2) if current_price else 0
            item["prediction"] = {
                "current_price": current_price,
                "predicted_5d":  predicted_5d,
                "change_pct":    change_pct,
                "trend":         "상승" if change_pct > 1 else ("하락" if change_pct < -1 else "보합"),
            }
            # prediction 포함 재평가
            result         = await evaluate(code, prediction=pred)
            item["score"]   = result["score"]
            item["signal"]  = result["signal"]
            item["summary"] = result["summary"]
            item["factors"] = result["factors"]
        except Exception as e:
            logger.warning(f"Prediction failed for {code}: {e}")
            item["prediction"] = None
        return item

    pred_sem = asyncio.Semaphore(2)  # CPU 부하 제한

    async def _lim(item: dict):
        async with pred_sem:
            return await _eval_pred(item)

    enhanced = await asyncio.gather(*[_lim(c) for c in candidates])
    enhanced.sort(key=lambda x: x["score"], reverse=True)
    top10 = list(enhanced)[:10]

    _recommend_cache[cache_key] = (time.time() + _RECOMMEND_TTL, top10)
    return top10


@router.get("/{code}/price")
async def price(code: str):
    try:
        return await kis.price(code)
    except Exception as e:
        raise HTTPException(502, f"KIS API error: {e}")


@router.get("/{code}/daily")
async def daily(code: str, count: int = 60):
    try:
        return await kis.daily(code, count)
    except Exception as e:
        raise HTTPException(502, f"KIS API error: {e}")
