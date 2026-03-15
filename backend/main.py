# cd backend 
# uvicorn main:app --host 0.0.0.0 --port 8000 --reload
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from api import stock_router, trade_router, ws_router, ai_router, predict_router, backtest_router, manager, price_loop
from service.trading.bot import bot
from service.kis import kis
from service.market.price_sync import price_sync
from service.market.sector import load_sectors
from service.market.stock_universe import listing
from service.market.tick_queue import tick_q

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 앱 시작/종료 시 KIS 클라이언트 및 봇 라이프사이클 관리
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading all stock listings")
    listing()
    load_sectors()
    logger.info("Starting KIS API")
    kis_ok = False
    try:
        await kis.start()
        kis_ok = True
        logger.info("KIS API ready")
    except Exception as e:
        logger.warning("KIS API 인증 실패 (장외 시간 또는 키 문제) — 서버는 기동합니다: %s", e)

    if kis_ok:
        await tick_q.start()

    bot.on_message = manager.message
    bot.on_trade = manager.trade

    task = asyncio.create_task(price_loop()) if kis_ok else None
    yield

    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if bot.running:
        await bot.stop()
    await price_sync.flush_day()
    if kis_ok:
        await kis.ws_close()
        await tick_q.stop()
        await kis.stop()
    logger.info("Shutdown complete")

app = FastAPI(title="KI AutoTrade API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stock_router)
app.include_router(trade_router)
app.include_router(ws_router)
app.include_router(ai_router)
app.include_router(predict_router)
app.include_router(backtest_router)

# 서버 상태 확인 (봇 실행 여부 포함)
@app.get("/api/health")
async def health():
    return {"status": "ok", "trading_bot": bot.running}

# 프로덕션: SvelteKit 빌드 정적 파일 서빙
frontend_build = Path(__file__).parent.parent / "frontend" / "build"
if frontend_build.exists():
    app.mount("/", StaticFiles(directory=str(frontend_build), html=True), name="frontend")
