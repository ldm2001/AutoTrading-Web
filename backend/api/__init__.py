from api.stock import router as stock_router
from api.trade import router as trade_router
from api.ws import router as ws_router, manager, loop as price_loop
from api.ai import router as ai_router
from api.predict import router as predict_router
