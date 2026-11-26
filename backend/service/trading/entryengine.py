# 매수 진입 — 시장 국면 게이트 + 멀티팩터 평가 + 주문 실행
import datetime
import logging
import secrets
from pathlib import Path
from config import settings
from service.trading.regime import regime as mktregime
from service.trading.research import wfin, wfverdict
from service.trading.strategy import verdict

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
        # 평가 기록 폴더(None이면 research/evals)와 시각 함수 — 테스트가 바꿈
        self.evals: Path | None = None
        self.clock = datetime.datetime.now
        # 종목별 마지막으로 기록한 15분 구간 시작 시각
        self._slots: dict[str, datetime.datetime] = {}

    # 평가 한 줄 — 종목마다 15분 구간당 1회, 매수 신호는 매번. 실패는 경고만 (매매를 막지 않음), eval_id 반환
    # 구간은 쓰기 전에 표시 — 폴더 고장 시 경고가 평가마다 쏟아지지 않게
    def ledger(self, sym: str, result: dict, prediction: dict | None) -> str:
        eval_id = ""
        try:
            eval_id = secrets.token_hex(6)
            now = self.clock()
            slot = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
            if result.get("signal") != "buy" and self._slots.get(sym) == slot:
                return eval_id
            self._slots[sym] = slot
            # 예측 요약 — 기준가·기준일·5일차 예측 종가 (학습 지표 등 나머지는 남기지 않음)
            summary = None
            if prediction:
                preds = prediction.get("predictions") or []
                summary = {
                    "base_close": prediction.get("base_close"),
                    "base_date": prediction.get("base_date"),
                    "close5": preds[4].get("close") if len(preds) >= 5 else None,
                }
            wfverdict({
                "kind": "eval",
                "time": now.isoformat(timespec="seconds"),
                "code": sym,
                "eval_id": eval_id,
                "score": result.get("score"),
                "signal": result.get("signal"),
                "factors": [
                    {"name": f.get("name"), "score": f.get("score"), "reason": f.get("reason")}
                    for f in result.get("factors") or []
                ],
                "price": result.get("price"),
                "stop_price": result.get("stop_price"),
                "prediction": summary,
                "use_prediction": bool(settings.use_prediction),
            }, root=self.evals)
        except Exception as e:
            logger.warning("Evaluation log failed (%s): %s", sym, e)
        return eval_id

    # 주문 결과 한 줄 — 평가와 같은 eval_id. 실패는 경고만
    def receipt(self, eval_id: str, sym: str, qty: int, order: dict) -> None:
        try:
            wfverdict({
                "kind": "order",
                "time": self.clock().isoformat(timespec="seconds"),
                "code": sym,
                "eval_id": eval_id,
                "success": bool(order.get("success")),
                "qty": qty,
            }, root=self.evals)
        except Exception as e:
            logger.warning("Order log failed (%s): %s", sym, e)

    # 시장 국면 게이트 — risk_off·지수 조회 실패 시 신규매수 차단 (fail-closed, stale 지수 금지)
    async def gate(self) -> bool:
        try:
            regime = mktregime(await self.broker.indices(stale=False))
        except Exception as e:
            logger.warning("Market regime check failed: %s", e)
            regime = {"state": "unknown", "allow_new_buys": False, "reason": f"지수 조회 실패: {e}"}

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
            # 예측 데이터 — 캐시만 사용 (학습을 기다리지 않음), 없으면 뒤에서 학습 시작하고 이번 평가는 예측 없이
            prediction = None
            if settings.use_prediction:
                try:
                    from service.ai.predict import predictor
                    prediction = predictor.cached(sym)
                    if prediction is None:
                        predictor.warmup(sym)
                except Exception as e:
                    logger.warning("Prediction cache lookup failed (%s): %s", sym, e)

            # 전략 엔진 평가
            result = await verdict(sym, prediction)
            # 평가 기록 — 매수 조건보다 먼저 (매수하지 않은 평가도 남김)
            eval_id = self.ledger(sym, result, prediction)
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

            order = await self.broker.purchase(sym, qty)
            self.receipt(eval_id, sym, qty, order)
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
