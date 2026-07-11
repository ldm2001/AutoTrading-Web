# 멀티팩터 자동매매 봇 — 매수/손절/익절/장마감 청산 루프
import asyncio
import datetime
import logging
from collections.abc import Callable
from config import settings
from service.trading.notifier import Notifier
from service.kis import KIS, NAMES, kis
from service.trading.entryengine import EntryEngine
from service.trading.journal import TradeJournal
from service.trading.positionbook import PositionBook
from service.trading.riskmonitor import RiskMonitor
from service.market.holidays import mkt
from service.market.tick_queue import TickQueue, tick_q
from service.trading.watchlist import symbols as watchlist_symbols
from service.infra.event_bus import bus

logger = logging.getLogger(__name__)

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
        self._task: asyncio.Task | None = None
        self.notifier = Notifier()
        self.positions = PositionBook(self.broker, self.names)
        self.journal = TradeJournal(self.notifier, self.positions.snap)
        self.risks = RiskMonitor(self.broker, self.positions, self.journal, self.notifier, self.names)
        self.entries = EntryEngine(self.broker, self.positions, self.journal, self.notifier, self.names)
        self._tick_event = asyncio.Event()
        self._last_tick_code: str = ""
        self._unsub_tick: Callable | None = None
        self.crashed: bool = False

    # 보유 포지션 상태 (PositionBook 위임 — 공개/테스트 호환)
    @property
    def bought(self) -> dict[str, dict]:
        return self.positions.bought

    @bought.setter
    def bought(self, v: dict[str, dict]) -> None:
        self.positions.bought = v

    @property
    def pending_buys(self) -> set[str]:
        return self.positions.pending_buys

    @pending_buys.setter
    def pending_buys(self, v: set[str]) -> None:
        self.positions.pending_buys = v

    # Redis 영속 위임 (재시작 복원 — supervisor/테스트 시임)
    def redo(self) -> None:
        self.positions.redo()

    # 봇 시작 — 오늘 거래 내역 복원 후 루프 실행
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.crashed = False
        self.positions.reset()
        self.risks.reset()
        self.redo()
        self.journal.load()

        # 복원 대조 — 봇 미추적 보유 경보 (bot:state 유실·수동 보유 가시화), 실패해도 기동 계속
        try:
            stray = await self.positions.untracked()
            if stray:
                names = ", ".join(f"{i.get('name') or self.names.get(c, c)}({c})" for c, i in stray.items())
                await self.msg(f"[복원 대조] 봇 미추적 보유 {len(stray)}종목 — {names} (손절/익절/장마감 청산 제외)")
        except Exception as e:
            logger.warning("Untracked holdings check failed: %s", e)

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
            "today_trades": self.journal.logs,
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

    # 공개 호환 — main이 bot.onmessage로 주입 → Notifier로 라우팅
    @property
    def onmessage(self) -> Callable | None:
        return self.notifier.onmessage

    @onmessage.setter
    def onmessage(self, cb: Callable | None) -> None:
        self.notifier.onmessage = cb

    # 공개 호환 — main이 bot.ontrade로 주입 → TradeJournal로 라우팅
    @property
    def ontrade(self) -> Callable | None:
        return self.journal.ontrade

    @ontrade.setter
    def ontrade(self, cb: Callable | None) -> None:
        self.journal.ontrade = cb

    # 대기 매수 재대조 위임 (supervisor 재시작 경로 + 테스트 시임)
    async def pend(self) -> None:
        await self.positions.pend()

    # 장마감 청산 위임 (RiskMonitor)
    async def sellpos(self) -> None:
        await self.risks.sellpos()

    # 손절 카운터 상태 (RiskMonitor 위임 — 테스트 호환)
    @property
    def _sl_fails(self) -> dict[str, int]:
        return self.risks._sl_fails

    @_sl_fails.setter
    def _sl_fails(self, v: dict[str, int]) -> None:
        self.risks._sl_fails = v

    @property
    def _risk_last(self) -> float:
        return self.risks._risk_last

    @_risk_last.setter
    def _risk_last(self, v: float) -> None:
        self.risks._risk_last = v

    # 손절/익절 감시 위임 (RiskMonitor)
    async def riskgate(self) -> None:
        await self.risks.riskgate()

    # 장중 여부 (개장일 09:05~15:15) — KST 명시는 Phase 2에서
    def hours(self) -> bool:
        now = datetime.datetime.now()
        if not mkt(now.date()):
            return False
        t_start = now.replace(hour=9, minute=5, second=0, microsecond=0)
        t_sell = now.replace(hour=15, minute=15, second=0, microsecond=0)
        return t_start <= now < t_sell

    # 손절/익절 모니터링 위임 (RiskMonitor)
    async def risk(self) -> None:
        await self.risks.risk()

    # 매수 진입 위임 (EntryEngine)
    async def ent(self, sym: str, buy_amount: float) -> None:
        await self.entries.ent(sym, buy_amount)

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

                if not mkt(now.date()):
                    await self.msg("주말/휴장일이므로 프로그램을 종료합니다.")
                    break

                # 09:05 ~ 15:15
                if t_start < now < t_sell:

                    # 손절/익절 체크 — riskgate가 2초 스로틀 적용
                    await self.riskgate()

                    # 매수 스캔 (5분마다, 동적 워치리스트)
                    if now.minute != last_eval_min and now.minute % 5 == 0:
                        last_eval_min = now.minute
                        if not await self.entries.gate():
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
