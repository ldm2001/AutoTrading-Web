# 매매/봇/워치리스트 API 라우터
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from schema import OrderRequest
from service import kis, bot

router = APIRouter(prefix="/api/trading")

# 워치리스트 파일 경로
_WATCHLIST_FILE = Path(__file__).resolve().parent.parent / "watchlist.json"


# 워치리스트 파일 읽기
def _load() -> list[str]:
    if _WATCHLIST_FILE.exists():
        try:
            return json.loads(_WATCHLIST_FILE.read_text())
        except Exception:
            pass
    from config import settings
    return list(settings.symbol_list)


# 워치리스트 파일 저장
def _save(codes: list[str]) -> None:
    _WATCHLIST_FILE.write_text(json.dumps(codes, ensure_ascii=False))


# 봇 스캔 대상 종목 목록
def symbols() -> list[str]:
    return _load()


class WatchlistBody(BaseModel):
    codes: list[str]


# 워치리스트 조회
@router.get("/watchlist")
async def watchlist():
    return {"codes": _load()}


# 워치리스트 수정
@router.put("/watchlist")
async def edit_watchlist(body: WatchlistBody):
    codes = [c.strip() for c in body.codes if c.strip()]
    _save(codes)
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
    except Exception as e:
        raise HTTPException(502, f"KIS API error: {e}")


# 주문 가능 예수금 조회
@router.get("/balance")
async def balance():
    try:
        return {"cash": await kis.cash()}
    except Exception as e:
        raise HTTPException(502, f"KIS API error: {e}")


# 자동매매 봇 시작
@router.post("/bot/start")
async def start():
    if bot.running:
        raise HTTPException(409, "Bot is already running")
    await bot.start()
    return {"status": "started"}


# 자동매매 봇 중지
@router.post("/bot/stop")
async def stop():
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
async def buy(order: OrderRequest):
    try:
        return await kis.buy(order.code, order.qty)
    except Exception as e:
        raise HTTPException(502, f"Buy failed: {e}")


# 시장가 매도 주문
@router.post("/sell")
async def sell(order: OrderRequest):
    try:
        return await kis.sell(order.code, order.qty)
    except Exception as e:
        raise HTTPException(502, f"Sell failed: {e}")


# 거래 내역 조회 (날짜별, 기본=오늘)
@router.get("/history")
async def history(date: str | None = None):
    from service.bot import _load_trades
    return {"date": date or "today", "trades": _load_trades(date)}


# 하위 호환 별칭
get_bot_symbols = symbols
