# 종목 데이터 및 AI 추천 API 라우터
import asyncio
import time
import logging
from fastapi import APIRouter, HTTPException
from service.kis import kis
from service.trading.strategy import evaluate
from service.ai.predict import predict_stock, predictor
from service.market.stock_universe import ALL_STOCKS, CODES, NAMES, search

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stocks")

# AI 추천 캐시
_STAGE1_TTL = 120
_STAGE2_TTL = 600
_ENHANCE_LIMIT = 10
_stage1_cache: tuple[float, dict] | None = None
_stage2_cache: tuple[float, list[dict]] | None = None
_generation = 0
# 1단계
_stage1_job: asyncio.Task | None = None
# 2단계
_stage2_job: asyncio.Task | None = None

# ETF 제외 개별 종목 (추천 스캔 대상)
_SCAN_CODES = [
    code for code, name in NAMES.items()
    if not any(tag in name for tag in ["KODEX", "TIGER", "ETF"])
]

# 만료 전 캐시만 반환
def live(entry):
    if entry is None:
        return None
    expires_at, value = entry
    if time.time() < expires_at:
        return value
    return None

# 만료 여부와 관계없이 마지막 값을 반환
def peek(entry):
    if entry is None:
        return None
    return entry[1]

# 예측 결과를 추천 요약으로 변환
def brief(current_price: int, pred: dict | None) -> dict | None:
    if not pred:
        return None
    preds = pred.get("predictions", [])
    if not preds:
        return None
    predicted_5d = preds[-1]["close"]
    change_pct = round((predicted_5d - current_price) / current_price * 100, 2) if current_price else 0
    return {
        "current_price": current_price,
        "predicted_5d": predicted_5d,
        "change_pct": change_pct,
        "trend": "상승" if change_pct > 1 else ("하락" if change_pct < -1 else "보합"),
    }

# 추천 응답 형식 통일
def pack(items: list[dict], *, stage: str, enhancing: bool) -> dict:
    return {
        "items": items,
        "stage": stage,
        "enhancing": enhancing,
    }

# 진행 중 작업 여부 확인
def busy(job: asyncio.Task | None) -> bool:
    return job is not None and not job.done()

# 전체 상장사 목록
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

# 종목 검색
@router.get("/search")
async def hits(q: str):
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

# 1단계 추천 후보를 계산
async def screen() -> dict:
    global _stage1_cache, _stage2_cache, _generation, _stage1_job
    task = asyncio.current_task()
    try:
        sem = asyncio.Semaphore(10)

        # 1단계 스크리닝 작업
        async def slot(code: str):
            async with sem:
                try:
                    cached_pred = predictor.cached(code)
                    result = await evaluate(code, prediction=cached_pred, fast=True)
                    return {
                        "code":       code,
                        "name":       NAMES.get(code, code),
                        "signal":     result["signal"],
                        "score":      result["score"],
                        "price":      result["price"],
                        "summary":    result["summary"],
                        "factors":    result["factors"],
                        "prediction": brief(result["price"], cached_pred),
                    }
                except Exception as e:
                    logger.debug(f"Recommend eval skip {code}: {e}")
                    return None

        results = await asyncio.gather(*[slot(code) for code in _SCAN_CODES])

        valid = [r for r in results if r is not None and r["score"] != 0]
        valid.sort(key=lambda x: x["score"], reverse=True)
        top10 = valid[:10]

        _generation += 1
        _stage2_cache = None
        block = {
            "generation": _generation,
            "items": top10,
            "candidates": [dict(item) for item in valid[:_ENHANCE_LIMIT]],
        }
        _stage1_cache = (time.time() + _STAGE1_TTL, block)
        return block
    finally:
        if _stage1_job is task:
            _stage1_job = None

# 2단계 백그라운드 — 트랜스포머 예측으로 캐시 갱신
async def stage2(candidates: list[dict], generation: int) -> None:
    global _stage2_cache, _stage2_job
    task = asyncio.current_task()
    try:
        pred_sem = asyncio.Semaphore(3)

        # 2단계 예측 작업
        async def slot(item: dict) -> dict:
            async with pred_sem:
                code = item["code"]
                try:
                    pred  = await predict_stock(code)
                    result = await evaluate(code, prediction=pred, fast=False)
                    item["prediction"] = brief(result["price"], pred)
                    item["score"]   = result["score"]
                    item["signal"]  = result["signal"]
                    item["summary"] = result["summary"]
                    item["factors"] = result["factors"]
                    item["price"]   = result["price"]
                except Exception as e:
                    logger.warning(f"Prediction failed for {code}: {e}")
                    item["prediction"] = None
                return item

        enhanced = await asyncio.gather(*[slot(c) for c in candidates])
        enhanced.sort(key=lambda x: x["score"], reverse=True)
        top10 = list(enhanced)[:10]
        if generation == _generation:
            _stage2_cache = (time.time() + _STAGE2_TTL, top10)
        logger.info(f"Recommend enhanced with predictions ({len(top10)} stocks)")
    except Exception as e:
        logger.error(f"Enhance background failed: {e}")
    finally:
        if _stage2_job is task:
            _stage2_job = None

# AI 멀티팩터 + 트랜스포머 2단계 추천
@router.get("/recommend")
async def recommend():
    global _stage1_job, _stage2_job

    enhanced = live(_stage2_cache)
    if enhanced is not None:
        return pack(enhanced, stage="enhanced", enhancing=False)

    stage1 = live(_stage1_cache)
    # 갱신 중이면 마지막 1단계 결과를 계속 유지
    if stage1 is None and (busy(_stage1_job) or busy(_stage2_job)):
        stage1 = peek(_stage1_cache)
    if stage1 is not None:
        should_enhance = bool(stage1["candidates"]) and not busy(_stage2_job)
        if should_enhance:
            _stage2_job = asyncio.create_task(stage2(
                [dict(item) for item in stage1["candidates"]],
                stage1["generation"],
            ))
        return pack(
            stage1["items"],
            stage="screened",
            enhancing=busy(_stage2_job) or should_enhance,
        )

    if not busy(_stage1_job):
        _stage1_job = asyncio.create_task(screen())
    stage1 = await _stage1_job

    # 2단계: 백그라운드로 Transformer 예측 보강 (상위 일부)
    candidates = [dict(item) for item in stage1["candidates"]]
    if candidates and not busy(_stage2_job):
        _stage2_job = asyncio.create_task(stage2(candidates, stage1["generation"]))

    return pack(stage1["items"], stage="screened", enhancing=busy(_stage2_job))

@router.get("/{code}/orderbook")
async def orderbook(code: str):
    try:
        return await kis.orderbook(code)
    except Exception as e:
        raise HTTPException(502, f"KIS API error: {e}")

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
