# cd backend
# uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from prometheus_fastapi_instrumentator import Instrumentator
from config import settings
from api import stock_router, trade_router, ws_router, ai_router, predict_router, backtest_router, manager, price_loop
from api.security import ALLOWED_ORIGINS, MUTATING_METHODS, csrfok
from api.limiter import limiter
from service.trading.bot import bot
from service.kis import kis
from service import discord
from service.market.price_sync import price_sync
from service.market.sector import sectors
from service.market.stock_universe import listing
from service.market.tick_queue import tick_q
from service.event_bus import bus
from service.logging import setup as setup_logging

# 구조화 로깅 초기화
setup_logging()
logger = logging.getLogger(__name__)

# 앱 시작/종료 시 KIS 클라이언트 및 봇 라이프사이클 관리
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading all stock listings")
    listing()
    sectors()
    mode = "모의투자(MOCK)" if settings.mock else "⚠️ 실전투자(LIVE)"
    logger.warning("주문 모드: %s | 계좌 %s**** | %s", mode, settings.cano[:4], settings.url_base)
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

    # 이벤트 버스 시작 (Redis Pub/Sub 연결)
    if kis.cache.redis is not None:
        bus.bind(kis.cache.redis)
    await bus.start()

    # Discord 알림 큐 기동 (주문 경로 비차단)
    await discord.start()

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
    # 봇 종료 메시지까지 drain 후 큐 정리
    await discord.close()
    await bus.stop()
    await price_sync.eod()
    if kis_ok:
        await kis.wclose()
        await tick_q.stop()
        await kis.stop()
    logger.info("Shutdown complete")

# 하위 호환 테스트/설정을 위한 별칭
_ALLOWED_ORIGINS = ALLOWED_ORIGINS
_MUTATING_METHODS = MUTATING_METHODS

# Prometheus는 전역 레지스트리 — 프로세스당 1회만 계측 (create_app 재호출/테스트 대비)
_INSTRUMENTED = False

# 보안 헤더 미들웨어 (CSRF 검증 + XSS/클릭재킹 방지)
class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # CSRF: 상태 변경 요청 시 Origin 검증, Origin 부재 시 X-API-Key 헤더 필수
        if request.method in _MUTATING_METHODS:
            if not csrfok(request.headers.get("origin"), request.headers.get("x-api-key")):
                return Response("Forbidden: origin or api key required", status_code=403)

        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

# FastAPI 앱 생성 — api_key 유무에 따라 매매 라우터 조건 등록 (테스트 재사용)
def create_app() -> FastAPI:
    app = FastAPI(
        title="KI AutoTrade API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    # 레이트리미터 배선 — 미배선 시 한도 초과가 429 대신 500(AttributeError)
    app.state.limiter = limiter

    # 보안 헤더 미들웨어 등록
    app.add_middleware(SecurityHeaders)

    # CORS 미들웨어 등록
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(_ALLOWED_ORIGINS),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    # 레이트 리밋 초과 핸들러 등록
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # API 라우터 등록 — 매매 라우터는 api_key 설정 시에만 (fail-safe)
    app.include_router(stock_router)
    if settings.api_key:
        app.include_router(trade_router)
    else:
        logger.warning("API_KEY 미설정 — 매매 API(/api/trading/*) 비활성화. 조회 기능만 동작합니다")
    app.include_router(ws_router)
    app.include_router(ai_router)
    app.include_router(predict_router)
    app.include_router(backtest_router)

    # Prometheus 메트릭 엔드포인트 등록 (프로세스당 1회 — 중복 등록 방지)
    global _INSTRUMENTED
    if not _INSTRUMENTED:
        Instrumentator().instrument(app).expose(app, endpoint="/metrics")
        _INSTRUMENTED = True

    # 서버 상태 확인 (봇 실행/사망 여부, 매매 API 활성 여부 포함)
    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "trading_bot": bot.running,
            "bot_crashed": bot.crashed,
            "trading_api": bool(settings.api_key),
            "mode": "mock" if settings.mock else "real",
            "redis": kis.cache.redis is not None,
            "kafka": tick_q.kafkaon,
        }

    # 프로덕션: SvelteKit 빌드 정적 파일 서빙
    frontend_build = Path(__file__).parent.parent / "frontend" / "build"
    if frontend_build.exists():
        app.mount("/", StaticFiles(directory=str(frontend_build), html=True), name="frontend")

    return app

# ASGI 진입점 (uvicorn/gunicorn main:app)
app = create_app()
