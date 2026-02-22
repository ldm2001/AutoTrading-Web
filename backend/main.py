# cd backend 
# uvicorn main:app --host 0.0.0.0 --port 8000 --reload
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from api import stock_router, trade_router, ws_router, ai_router, predict_router, manager, price_loop
from service import kis, bot, load_all_stocks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 앱 시작/종료 시 KIS 클라이언트 및 봇 라이프사이클 관리
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading all stock listings")
    load_all_stocks()
    logger.info("Starting KIS API")
    await kis.start()
    logger.info("KIS API ready")

    bot.on_message = manager.message
    bot.on_trade = manager.trade

    task = asyncio.create_task(price_loop())
    yield

    task.cancel()
    if bot.running:
        await bot.stop()
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

# 서버 상태 확인 (봇 실행 여부 포함)
@app.get("/api/health")
async def health():
    return {"status": "ok", "trading_bot": bot.running}

# 프로덕션: SvelteKit 빌드 정적 파일 서빙
frontend_build = Path(__file__).parent.parent / "frontend" / "build"
if frontend_build.exists():
    app.mount("/", StaticFiles(directory=str(frontend_build), html=True), name="frontend")
