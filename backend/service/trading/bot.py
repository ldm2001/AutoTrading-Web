# 멀티팩터 자동매매 봇 — 매수/손절/익절/장마감 청산 루프
import asyncio
import datetime
import json
import logging
import time
from collections.abc import Callable
from config import settings
from service.trading.notifier import Notifier
from service.kis import KIS, NAMES, kis
from service.trading.regime import regime as regime_state
from service.trading.research import wfin, wfout
from service.trading.trade_log import append as trade_log_append, rows as trade_log_rows
from service.trading.strategy import evaluate
from service.trading.stop_loss import stop_loss
from service.market.tick_queue import TickQueue, tick_q
from service.trading.watchlist import symbols as watchlist_symbols
from service.event_bus import bus

logger = logging.getLogger(__name__)

_REDIS_BOT_KEY = "bot:state"

# 예외 재시작 백오프 (초) — 길이 = 최대 재시도 횟수
_BACKOFF = (30, 120, 600)

# Bot 클래스 — 매수/손절/익절/장마감 자동매매 루프
class Bot:
    # 봇 상태 초기화 — running/bought/logs/콜백 세팅
    def __init__(
        self,
        broker: KIS,
        queue: TickQueue,
        names: dict[str, str],
        scan: Callable[[], list[str]],
    ) -> None:
        self.broker = broker
        self.queue = queue
        self.names = names
        self.scan = scan
        self.running: bool = False
        self.bought: dict[str, dict] = {} # {"avg_price": int, "qty": int, "name": str}
        self.pending_buys: set[str] = set()
        self.pending_stops: dict[str, float] = {}
        self.logs: list[dict] = []
        self._task: asyncio.Task | None = None
        self.notifier = Notifier()
        self.on_trade: Callable | None = None
        self._tick_event = asyncio.Event()
        self._last_tick_code: str = ""
        self._unsub_tick: Callable | None = None
        self._last_regime_state: str | None = None
        self._sl_fails: dict[str, int] = {}
        self._risk_last: float = 0.0
        self.crashed: bool = False

    # Redis에 봇 보유 상태 저장
    def snap(self) -> None:
        r = self.broker.cache.redis
        if r is None:
            return
        try:
            r.set(_REDIS_BOT_KEY, json.dumps(self.bought, default=str))
        except Exception as e:
            logger.warning("Bot state save failed: %s", e)

    # Redis에서 봇 보유 상태 복원
    def redo(self) -> None:
        r = self.broker.cache.redis
        if r is None:
            return
        try:
            raw = r.get(_REDIS_BOT_KEY)
            if raw:
                self.bought = json.loads(raw)
                logger.info("Bot state restored from Redis: %s positions", len(self.bought))
        except Exception as e:
            logger.warning("Bot state restore failed: %s", e)

    # 봇 시작 — 오늘 거래 내역 복원 후 루프 실행
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.crashed = False
        self.bought = {}
        self.pending_buys = set()
        self.pending_stops = {}
        self._sl_fails = {}
        self._risk_last = 0.0
        self.redo()
        self.logs = trade_log_rows()
        await self.queue.start()

        # 이벤트 버스 구독 — tick 이벤트로 보유종목 실시간 체크
        def tickcb(_event: str, data: dict) -> None:
            if data and data.get("code") in self.bought:
                self._last_tick_code = data["code"]
                self._tick_event.set()
        self._unsub_tick = bus.on("tick", tickcb)

        self._task = asyncio.create_task(self.run())
        await self.msg(f"=== 자동매매 시작 ({'모의투자' if settings.mock else '실전투자'}) ===")

    # 봇 중지 — tick 핸들러 해제 후 봇 루프 종료
    async def stop(self) -> None:
        self.running = False
        if self._unsub_tick:
            self._unsub_tick()
            self._unsub_tick = None
        if self._task:
            self._task.cancel()
            self._task = None
        await self.msg("=== 자동매매 종료 ===")

    # 현재 봇 상태 반환 (실행 여부, 보유 종목, 오늘 거래 내역)
    def status(self) -> dict:
        return {
            "is_running": self.running,
            "crashed": self.crashed,
            "bought_list": list(self.bought.keys()),
            "today_trades": self.logs,
            "watch_count": len(self.scan()),
            "plan": {
                "target_buy_count": settings.target_buy_count,
                "buy_percent": settings.buy_percent,
                "stop_loss_pct": settings.stop_loss_pct,
                "take_profit_pct": settings.take_profit_pct,
                "buy_score_threshold": settings.buy_score_threshold,
                "market_filter": "risk_off 신규매수 차단",
            },
        }

    # 알림 위임 — Notifier가 로그/Discord/WS 라우팅
    async def msg(self, text: str) -> None:
        await self.notifier.msg(text)

    # 공개 호환 — main이 bot.on_message로 주입 → Notifier로 라우팅
    @property
    def on_message(self) -> Callable | None:
        return self.notifier.on_message

    @on_message.setter
    def on_message(self, cb: Callable | None) -> None:
        self.notifier.on_message = cb

    # 거래 내역 기록 — 파일 저장 + WebSocket 이벤트 발행
    async def rec(
        self, code: str, name: str, kind: str, qty: int, price: int, ok: bool
    ) -> None:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "time": now,
            "code": code,
            "name": name,
            "type": kind,
            "qty": qty,
            "price": price,
            "success": ok,
            "message": "성공" if ok else "실패",
        }
        self.logs.append(entry)
        trade_log_append(entry)
        self.snap()
        action = "매수" if kind == "buy" else "매도"
        await self.msg(f"[{action} {'성공' if ok else '실패'}] {name or code} {qty}주 @{price:,}")
        if self.on_trade:
            await self.on_trade(entry)

    def acct(self, code: str, info: dict, stop_price: float | None = None) -> dict:
        pos = {
            "avg_price": int(info.get("avg_price", 0)),
            "qty": int(info.get("qty", 0)),
            "name": info.get("name") or self.names.get(code, code),
        }
        if stop_price is not None:
            pos["stop_price"] = stop_price
        elif code in self.bought and self.bought[code].get("stop_price") is not None:
            pos["stop_price"] = self.bought[code]["stop_price"]
        return pos

    def drop(self, code: str) -> None:
        if code in self.bought:
            del self.bought[code]
            self.snap()

    async def pos(self, code: str, stop_price: float | None = None) -> dict | None:
        items, _ = await self.broker.holdings()
        info = items.get(code)
        if not info:
            return None
        self.bought[code] = self.acct(code, info, stop_price)
        self.pending_buys.discard(code)
        self.pending_stops.pop(code, None)
        self.snap()
        return self.bought[code]

    async def pend(self) -> None:
        if not self.pending_buys:
            return
        items, _ = await self.broker.holdings()
        for code in list(self.pending_buys):
            info = items.get(code)
            if info:
                self.bought[code] = self.acct(code, info, self.pending_stops.get(code))
                self.pending_buys.discard(code)
                self.pending_stops.pop(code, None)
        self.snap()

    async def sellpos(self) -> None:
        items, _ = await self.broker.holdings()
        for code, tracked in list(self.bought.items()):
            info = items.get(code, tracked)
            qty = int(info.get("qty") or tracked.get("qty") or 0)
            if qty <= 0:
                self.drop(code)
                continue
            name = info.get("name") or tracked.get("name") or self.names.get(code, code)
            result = await self.broker.sell(code, qty)
            cp = int(info.get("current_price", 0))
            await self.rec(code, name, "sell", qty, cp, result["success"])
            if result["success"]:
                avg = int(info.get("avg_price") or tracked.get("avg_price") or 0)
                pnl = ((cp - avg) / avg * 100) if avg > 0 else None
                wfout(code, "eod", cp, qty, pnl)
                self.drop(code)
            else:
                self.bought[code] = self.acct(code, info)
                self.snap()

    async def gate(self) -> bool:
        try:
            regime = regime_state(await self.broker.indices())
        except Exception as e:
            logger.warning("Market regime check failed: %s", e)
            return True

        state = regime["state"]
        if state != self._last_regime_state:
            self._last_regime_state = state
            await self.msg(f"[시장 국면] {state} — {regime['reason']}")
        return bool(regime["allow_new_buys"])

    # 손절 평가 실패 누적 — 연속 3회 시 경보, 이후 20회마다 재경보
    async def slfail(self, code: str, info: dict, err: Exception) -> None:
        n = self._sl_fails.get(code, 0) + 1
        self._sl_fails[code] = n
        logger.warning("Stop-loss check failed (%s, consecutive=%s): %s", code, n, err)
        if n % 20 == 3:
            name = info.get("name") or self.names.get(code, code)
            await self.msg(f"[손절 모니터링 장애] {name}({code}) 시세 조회 {n}회 연속 실패")

    # 손절/익절 체크 게이트 — 최소 2초 간격 스로틀 (틱 이벤트 즉시 반응)
    async def riskgate(self) -> None:
        if not self.bought:
            return
        if time.monotonic() - self._risk_last < 2.0:
            return
        self._risk_last = time.monotonic()
        await self.risk()

    # 장중 여부 (평일 09:05~15:15) — KST 명시/공휴일 반영은 Phase 2에서
    def hours(self) -> bool:
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
        t_start = now.replace(hour=9, minute=5, second=0, microsecond=0)
        t_sell = now.replace(hour=15, minute=15, second=0, microsecond=0)
        return t_start <= now < t_sell

    # 손절/익절 모니터링 (동적 손절 지원)
    async def risk(self) -> None:
        for code, info in list(self.bought.items()):
            try:
                sp = info.get("stop_price")
                try:
                    should_stop, pnl = await stop_loss(
                        self.broker, code, info["avg_price"],
                        structural_price=sp,
                        fallback_pct=settings.stop_loss_pct,
                    )
                except Exception as err:
                    await self.slfail(code, info, err)
                    continue
                self._sl_fails.pop(code, None)

                if should_stop:
                    label = f"구조적 {sp:,.0f}" if sp else f"{settings.stop_loss_pct}%"
                    await self.msg(
                        f"[손절] {info['name']}({code}) 수익률 {pnl:.2f}% (기준: {label})"
                    )
                    result = await self.broker.sell(code, info["qty"])
                    cp = await self.broker.raw(code)
                    await self.rec(code, info["name"], "sell", info["qty"], cp, result["success"])
                    if result["success"]:
                        wfout(code, "stop", cp, info["qty"], pnl)
                        self.drop(code)
                    continue

                # 익절 체크
                if pnl >= settings.take_profit_pct:
                    await self.msg(
                        f"[익절] {info['name']}({code}) 수익률 +{pnl:.2f}% ≥ +{settings.take_profit_pct}%"
                    )
                    result = await self.broker.sell(code, info["qty"])
                    cp = await self.broker.raw(code)
                    await self.rec(code, info["name"], "sell", info["qty"], cp, result["success"])
                    if result["success"]:
                        wfout(code, "profit", cp, info["qty"], pnl)
                        self.drop(code)

            except Exception as e:
                logger.error(f"Holdings check error ({code}): {e}")

    # 멀티팩터 매수 판단 
    async def ent(self, sym: str, buy_amount: float) -> None:
        if sym in self.bought or sym in self.pending_buys:
            return
        keep_pending = False
        self.pending_buys.add(sym)
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
            await self.msg(
                f"[매수 시그널] {sym} 스코어={score:+.0f} ({', '.join(factor_strs)})"
            )

            # 동적 손절가 로그
            sp = result.get("stop_price")
            if sp:
                await self.msg(f"  → 구조적 손절가: {sp:,.0f}원")

            order = await self.broker.buy(sym, qty)
            name = self.names.get(sym, sym)
            if order["success"]:
                # walk-forward: 매수 진입 시점의 평가 결과 누적
                wfin(sym, result, qty)
                pos = await self.pos(sym, sp)
                if not pos:
                    keep_pending = True
                    if sp is not None:
                        self.pending_stops[sym] = sp
                    await self.msg(f"[매수 접수] {name}({sym}) 체결 잔고 반영 대기")
            await self.rec(sym, name, "buy", qty, cp, order["success"])

        except Exception as e:
            logger.error(f"Buy check error ({sym}): {e}")
        finally:
            if not keep_pending:
                self.pending_buys.discard(sym)
                self.pending_stops.pop(sym, None)

    # 슈퍼바이저 — loop 예외 시 경보 + 바운드 재시작 (장중 한정), 종료 시 구독 정리
    async def run(self) -> None:
        attempts = 0
        try:
            while True:
                try:
                    await self.loop()
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    held = ", ".join(f"{v.get('name', k)}({k})" for k, v in self.bought.items()) or "없음"
                    logger.exception("Bot crashed")
                    await self.msg(f"[봇 비정상 정지] {e} — 보유: {held}")
                    giveup = attempts >= len(_BACKOFF)
                    if not settings.bot_restart_on_crash or giveup or not self.hours():
                        self.crashed = True
                        if giveup:
                            await self.msg(f"[봇 재시작 포기] {len(_BACKOFF)}회 초과 — 수동 확인 필요")
                        break
                    attempts += 1
                    await self.msg(f"[봇 재시작 {attempts}/{len(_BACKOFF)}] {_BACKOFF[attempts - 1]}초 후 재시도")
                    await asyncio.sleep(_BACKOFF[attempts - 1])
                    self.redo()
                    await self.pend()
        finally:
            self.running = False
            if self._unsub_tick:
                self._unsub_tick()
                self._unsub_tick = None

    # 메인 루프
    async def loop(self) -> None:
        try:
            total_cash = await self.broker.cash()
            items, evaluation = await self.broker.holdings()

            buy_amount = total_cash * settings.buy_percent

            lines = ["==== 보유잔고 ===="]
            if items:
                for code, info in items.items():
                    name = info["name"]
                    qty = info["qty"]
                    avg = info.get("avg_price", 0)
                    cur = info.get("current_price", 0)
                    eval_amt = info.get("eval_amount", cur * qty)
                    pnl = info.get("profit_loss_percent", 0)
                    lines.append(
                        f"  {name}({code}) {qty}주 | "
                        f"매입 {avg:,}원 → 현재 {cur:,}원 | "
                        f"평가 {eval_amt:,}원 ({pnl:+.2f}%)"
                    )
            else:
                lines.append("  보유 종목 없음")
            lines.append(f"주식 평가: {int(evaluation.get('scts_evlu_amt', '0')):,}원")
            lines.append(f"평가 손익: {int(evaluation.get('evlu_pfls_smtl_amt', '0')):,}원")
            lines.append(f"총 평가: {int(evaluation.get('tot_evlu_amt', '0')):,}원")
            lines.append(f"매수 가능: {total_cash:,}원 (종목당 {buy_amount:,.0f}원)")
            lines.append(f"전략: 멀티팩터 (매수>{settings.buy_score_threshold}점, 손절=FVG동적(폴백{settings.stop_loss_pct}%), 익절+{settings.take_profit_pct}%)")
            lines.append("==================")
            await self.msg("\n".join(lines))

            soldout = False
            last_eval_min = -1

            # 메인 매매 루프
            while self.running:
                now = datetime.datetime.now()
                t_start = now.replace(hour=9, minute=5, second=0, microsecond=0)
                t_sell = now.replace(hour=15, minute=15, second=0, microsecond=0)
                t_exit = now.replace(hour=15, minute=20, second=0, microsecond=0)

                if now.weekday() >= 5:
                    await self.msg("주말이므로 프로그램을 종료합니다.")
                    break

                # 매매 시간대 (09:05 ~ 15:15)
                if t_start < now < t_sell:

                    # 손절/익절 체크 — riskgate가 2초 스로틀 적용 (틱 wake 즉시 반응)
                    await self.riskgate()

                    # 매수 스캔 (5분마다, 동적 워치리스트)
                    if now.minute != last_eval_min and now.minute % 5 == 0:
                        last_eval_min = now.minute
                        if not await self.gate():
                            continue
                        await self.pend()
                        total_cash = await self.broker.cash()
                        buy_amount = total_cash * settings.buy_percent
                        scan_list = self.scan()
                        for sym in scan_list:
                            if len(self.bought) >= settings.target_buy_count:
                                break
                            if sym in self.bought or sym in self.pending_buys:
                                continue
                            await self.ent(sym, buy_amount)
                            await asyncio.sleep(1)

                    # 30분마다 잔고 갱신
                    if now.minute % 30 == 0 and now.second < 10:
                        items, _ = await self.broker.holdings()

                # 장 마감 전 일괄 매도
                if t_sell < now < t_exit and not soldout:
                    await self.msg("[장마감] 보유 종목 일괄 매도")
                    await self.sellpos()
                    soldout = True

                if now > t_exit:
                    await self.msg("프로그램을 종료합니다.")
                    break

                # 이벤트 드리븐: tick 이벤트 또는 5초 타임아웃
                try:
                    await asyncio.wait_for(self._tick_event.wait(), timeout=5.0)
                    self._tick_event.clear()
                except TimeoutError:
                    pass

        except asyncio.CancelledError:
            logger.info("Bot cancelled")
            raise

# 모듈 레벨 Bot 싱글턴 인스턴스
bot = Bot(kis, tick_q, NAMES, watchlist_symbols)
