# 매매/봇/워치리스트 API 라우터
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from api.auth import require_key
from schema import OrderRequest
from service.trading.bot import bot
from service.kis import kis
from service.trading.order_log import rows
from service.trading.watchlist import load as load_watchlist, save as save_watchlist, symbols as watchlist_symbols

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/trading")

# 봇 스캔 대상 종목 목록
def symbols() -> list[str]:
    return watchlist_symbols()

class WatchlistBody(BaseModel):
    codes: list[str]

# 워치리스트 조회
@router.get("/watchlist")
async def watchlist():
    return {"codes": load_watchlist()}

# 워치리스트 수정
@router.put("/watchlist")
async def edit_watchlist(body: WatchlistBody, _key: str = Depends(require_key)):
    codes = [c.strip() for c in body.codes if c.strip()]
    save_watchlist(codes)
    return {"codes": codes, "count": len(codes)}

# 보유 종목 + 평가금액 + 예수금 조회
@router.get("/portfolio")
async def portfolio():
    try:
        items, evaluation = await kis.holdings()
        return {
            "items":             list(items.values()),
            "total_eval":        int(evaluation.get("tot_evlu_amt", "0")),
            "total_profit_loss": int(evaluation.get("evlu_pfls_smtl_amt", "0")),
            "cash_balance":      await kis.cash(),
        }
    except Exception:
        raise HTTPException(502, "서비스 일시 오류")

# 주문 가능 예수금 조회
@router.get("/balance")
async def balance():
    try:
        return {"cash": await kis.cash()}
    except Exception:
        raise HTTPException(502, "서비스 일시 오류")

# 자동매매 봇 시작
@router.post("/bot/start")
@limiter.limit("3/minute")
async def start(request: Request, _key: str = Depends(require_key)):
    if bot.running:
        raise HTTPException(409, "Bot is already running")
    await bot.start()
    return {"status": "started"}

# 자동매매 봇 중지
@router.post("/bot/stop")
@limiter.limit("3/minute")
async def stop(request: Request, _key: str = Depends(require_key)):
    if not bot.running:
        raise HTTPException(409, "Bot is not running")
    await bot.stop()
    return {"status": "stopped"}

# 봇 실행 상태 + 보유 종목 + 오늘 거래 내역
@router.get("/bot/status")
async def status():
    return bot.status()

# 시장가 매수 주문
@router.post("/buy")
@limiter.limit("5/minute")
async def buy(request: Request, order: OrderRequest, _key: str = Depends(require_key)):
    try:
        return await kis.buy(order.code, order.qty)
    except Exception:
        raise HTTPException(502, "주문 처리에 실패했습니다")

# 시장가 매도 주문
@router.post("/sell")
@limiter.limit("5/minute")
async def sell(request: Request, order: OrderRequest, _key: str = Depends(require_key)):
    try:
        return await kis.sell(order.code, order.qty)
    except Exception:
        raise HTTPException(502, "주문 처리에 실패했습니다")

# 섹터별 포트폴리오 히트맵 데이터
@router.get("/portfolio/heatmap")
async def heatmap():
    from service.market.sector import label
    try:
        items, _ = await kis.holdings()
        item_list = list(items.values())
        total_eval = sum(i["eval_amount"] for i in item_list) or 1

        sectors: dict[str, dict] = {}
        for item in item_list:
            sector = label(item["code"])
            if sector not in sectors:
                sectors[sector] = {"eval_amount": 0, "profit_loss": 0, "stocks": []}
            s = sectors[sector]
            s["eval_amount"] += item["eval_amount"]
            s["profit_loss"] += item["profit_loss"]
            s["stocks"].append({
                "code": item["code"],
                "name": item["name"],
                "profit_loss_pct": item["profit_loss_percent"],
            })

        result = []
        for name, s in sectors.items():
            weight = round(s["eval_amount"] / total_eval * 100, 1)
            avg_ret = round(
                sum(st["profit_loss_pct"] for st in s["stocks"]) / len(s["stocks"]), 2
            )
            result.append({
                "sector":      name,
                "weight_pct":  weight,
                "avg_return":  avg_ret,
                "eval_amount": s["eval_amount"],
                "profit_loss": s["profit_loss"],
                "stocks":      s["stocks"],
            })
        result.sort(key=lambda x: x["weight_pct"], reverse=True)
        return result
    except Exception:
        raise HTTPException(502, "서비스 일시 오류")

# 거래 내역 조회 (날짜별, 기본=오늘)
@router.get("/history")
async def history(date: str | None = None):
    return {"date": date or "today", "trades": rows(date)}

# 주문 감사 로그 조회
@router.get("/orders")
async def orders(date: str | None = None):
    return {"date": date or "today", "orders": rows(date)}
