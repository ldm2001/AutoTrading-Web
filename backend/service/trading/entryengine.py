# 매수 진입 — 시장 국면 게이트 + 멀티팩터 평가 + 주문 실행
import logging
from config import settings
from service.trading.regime import regime as regime_state
from service.trading.research import wfin
from service.trading.strategy import evaluate

logger = logging.getLogger(__name__)

# 워치리스트 종목의 매수 진입을 판단하고 주문
class EntryEngine:
    # broker(Quotes/Orders) + 포지션/기록/알림 협력자 주입
    def __init__(self, broker, positions, journal, notifier, names: dict[str, str]) -> None:
        self.broker = broker
        self.positions = positions
        self.journal = journal
        self.notifier = notifier
        self.names = names
        self._last_regime_state: str | None = None

    # 시장 국면 게이트 — risk_off 시 신규매수 차단, 조회 실패 시 허용(fail-open)
    async def gate(self) -> bool:
        try:
            regime = regime_state(await self.broker.indices())
        except Exception as e:
            logger.warning("Market regime check failed: %s", e)
            return True

        state = regime["state"]
        if state != self._last_regime_state:
            self._last_regime_state = state
            await self.notifier.msg(f"[시장 국면] {state} — {regime['reason']}")
        return bool(regime["allow_new_buys"])

    # 멀티팩터 매수 판단
    async def ent(self, sym: str, buy_amount: float) -> None:
        if sym in self.positions.bought or sym in self.positions.pending_buys:
            return
        keep_pending = False
        self.positions.pending_buys.add(sym)
        try:
            # 예측 데이터
            prediction = None
            if settings.use_prediction:
                try:
                    from service.ai.predict import predict_stock
                    prediction = await predict_stock(sym)
                except Exception:
                    pass

            # 전략 엔진 평가
            result = await evaluate(sym, prediction)
            signal = result["signal"]
            score = result["score"]

            if signal != "buy" or score < settings.buy_score_threshold:
                return

            cp = result["price"]
            qty = int(buy_amount // cp)
            if qty <= 0:
                return

            # 팩터 요약 로그
            factor_strs = [f"{f['name']}={f['score']:+.0f}" for f in result["factors"] if f["score"] != 0]
            await self.notifier.msg(
                f"[매수 시그널] {sym} 스코어={score:+.0f} ({', '.join(factor_strs)})"
            )

            # 동적 손절가 로그
            sp = result.get("stop_price")
            if sp:
                await self.notifier.msg(f"  → 구조적 손절가: {sp:,.0f}원")

            order = await self.broker.buy(sym, qty)
            name = self.names.get(sym, sym)
            if order["success"]:
                # walk-forward: 매수 진입 시점의 평가 결과 누적
                wfin(sym, result, qty)
                pos = await self.positions.pos(sym, sp)
                if not pos:
                    keep_pending = True
                    if sp is not None:
                        self.positions.pending_stops[sym] = sp
                    await self.notifier.msg(f"[매수 접수] {name}({sym}) 체결 잔고 반영 대기")
            await self.journal.rec(sym, name, "buy", qty, cp, order["success"])

        except Exception as e:
            logger.error(f"Buy check error ({sym}): {e}")
        finally:
            if not keep_pending:
                self.positions.pending_buys.discard(sym)
                self.positions.pending_stops.pop(sym, None)
