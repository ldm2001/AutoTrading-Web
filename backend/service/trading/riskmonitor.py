# 손절·익절 모니터링 + 연속 실패 경보 + 장마감 청산
import logging
import time
from config import settings
from service.trading.stoploss import stoploss
from service.trading.research import wfout

logger = logging.getLogger(__name__)

# 보유 종목의 손절/익절을 감시하고 청산
class RiskMonitor:
    # broker(Quotes/Orders/Account) + 포지션/기록/알림 협력자 주입
    def __init__(self, broker, positions, journal, notifier, names: dict[str, str]) -> None:
        self.broker = broker
        self.positions = positions
        self.journal = journal
        self.notifier = notifier
        self.names = names
        self._sl_fails: dict[str, int] = {}
        self._risk_last: float = 0.0

    # 봇 시작 시 카운터 초기화
    def reset(self) -> None:
        self._sl_fails = {}
        self._risk_last = 0.0

    # 손절 평가 실패 누적 — 연속 3회 시 경보, 이후 20회마다 재경보
    async def slfail(self, code: str, info: dict, err: Exception) -> None:
        n = self._sl_fails.get(code, 0) + 1
        self._sl_fails[code] = n
        logger.warning("Stop-loss check failed (%s, consecutive=%s): %s", code, n, err)
        if n % 20 == 3:
            name = info.get("name") or self.names.get(code, code)
            await self.notifier.msg(f"[손절 모니터링 장애] {name}({code}) 시세 조회 {n}회 연속 실패")

    # 손절/익절 체크 게이트 — 최소 2초 간격 스로틀 (틱 이벤트 즉시 반응)
    async def riskgate(self) -> None:
        if not self.positions.bought:
            return
        if time.monotonic() - self._risk_last < 2.0:
            return
        self._risk_last = time.monotonic()
        await self.risk()

    # 손절/익절 모니터링 (동적 손절 지원)
    async def risk(self) -> None:
        for code, info in list(self.positions.bought.items()):
            try:
                sp = info.get("stop_price")
                try:
                    should_stop, pnl = await stoploss(
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
                    await self.notifier.msg(
                        f"[손절] {info['name']}({code}) 수익률 {pnl:.2f}% (기준: {label})"
                    )
                    result = await self.broker.sell(code, info["qty"])
                    cp = await self.broker.raw(code)
                    await self.journal.rec(code, info["name"], "sell", info["qty"], cp, result["success"])
                    if result["success"]:
                        wfout(code, "stop", cp, info["qty"], pnl)
                        self.positions.drop(code)
                    continue

                # 익절 체크
                if pnl >= settings.take_profit_pct:
                    await self.notifier.msg(
                        f"[익절] {info['name']}({code}) 수익률 +{pnl:.2f}% ≥ +{settings.take_profit_pct}%"
                    )
                    result = await self.broker.sell(code, info["qty"])
                    cp = await self.broker.raw(code)
                    await self.journal.rec(code, info["name"], "sell", info["qty"], cp, result["success"])
                    if result["success"]:
                        wfout(code, "profit", cp, info["qty"], pnl)
                        self.positions.drop(code)

            except Exception as e:
                logger.error(f"Holdings check error ({code}): {e}")

    # 장 마감 전 보유 종목 일괄 매도
    async def sellpos(self) -> None:
        items, _ = await self.broker.holdings()
        for code, tracked in list(self.positions.bought.items()):
            info = items.get(code, tracked)
            qty = int(info.get("qty") or tracked.get("qty") or 0)
            if qty <= 0:
                self.positions.drop(code)
                continue
            name = info.get("name") or tracked.get("name") or self.names.get(code, code)
            result = await self.broker.sell(code, qty)
            cp = int(info.get("current_price", 0))
            await self.journal.rec(code, name, "sell", qty, cp, result["success"])
            if result["success"]:
                avg = int(info.get("avg_price") or tracked.get("avg_price") or 0)
                pnl = ((cp - avg) / avg * 100) if avg > 0 else None
                wfout(code, "eod", cp, qty, pnl)
                self.positions.drop(code)
            else:
                self.positions.bought[code] = self.positions.acct(code, info)
                self.positions.snap()
