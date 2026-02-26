# 멀티팩터 자동매매 봇 — 매수/손절/익절/장마감 청산 루프
import asyncio
import datetime
import json
import logging
from pathlib import Path
from collections.abc import Callable
from config import settings
from service.kis import kis
from service.discord import notify
from service.strategy import evaluate, stop_loss
from service.candle_store import store as candle_store
from service.tick_queue import tick_q

logger = logging.getLogger(__name__)

# 거래 내역 저장 디렉터리
_TRADES_DIR = Path(__file__).resolve().parent.parent / "trades"
_TRADES_DIR.mkdir(exist_ok=True)

# 날짜별 거래 내역 파일 경로 반환
def _trades_file(date: str | None = None) -> Path:
    if date is None:
        date = datetime.date.today().isoformat()
    return _TRADES_DIR / f"{date}.json"

# 날짜별 거래 내역 로드 (없으면 빈 리스트)
def _load_trades(date: str | None = None) -> list[dict]:
    f = _trades_file(date)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return []

# 거래 내역 파일에 단건 추가 저장
def _save_trade(entry: dict) -> None:
    today = datetime.date.today().isoformat()
    trades = _load_trades(today)
    trades.append(entry)
    _trades_file(today).write_text(json.dumps(trades, ensure_ascii=False, indent=2))

class Bot:
    # 봇 상태 초기화 — running/bought/logs/콜백 세팅
    def __init__(self) -> None:
        self.running: bool = False
        self.bought: dict[str, dict] = {} # {"avg_price": int, "qty": int, "name": str}
        self.logs: list[dict] = []
        self._task: asyncio.Task | None = None
        self.on_message: Callable | None = None
        self.on_trade: Callable | None = None

    # 봇 시작 — 오늘 거래 내역 복원 후 루프 실행
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.bought = {}
        self.logs = _load_trades()
        await tick_q.start()
        self._task = asyncio.create_task(self._run())
        await self._msg("=== 자동매매 시작 ===")

    # 봇 중지 — 캔들 flush + 틱큐 정지 후 종료
    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None
        saved = await candle_store.flush()
        await tick_q.stop()
        await self._msg(f"=== 자동매매 종료 (캔들 {saved}건 저장) ===")

    # 현재 봇 상태 반환 (실행 여부, 보유 종목, 오늘 거래 내역)
    def status(self) -> dict:
        return {
            "is_running": self.running,
            "bought_list": list(self.bought.keys()),
            "today_trades": self.logs,
        }

    # 로그 출력 + Discord 알림 + WebSocket 메시지 전송
    async def _msg(self, text: str) -> None:
        logger.info(text)
        await notify(text)
        if self.on_message:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await self.on_message(f"[{now}] {text}")

    # 거래 내역 기록 — 파일 저장 + WebSocket 이벤트 발행
    async def _log(
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
        _save_trade(entry)  # 파일 영구 저장
        action = "매수" if kind == "buy" else "매도"
        await self._msg(f"[{action} {'성공' if ok else '실패'}] {name or code} {qty}주 @{price:,}")
        if self.on_trade:
            await self.on_trade(entry)

    # ── 손절/익절 모니터링 (동적 손절 지원) ──
    async def _check_holdings(self) -> None:
        for code, info in list(self.bought.items()):
            try:
                sp = info.get("stop_price")
                should_stop, pnl = await stop_loss(
                    code, info["avg_price"],
                    structural_price=sp,
                    fallback_pct=settings.stop_loss_pct,
                )
                if should_stop:
                    label = f"구조적 {sp:,.0f}" if sp else f"{settings.stop_loss_pct}%"
                    await self._msg(
                        f"[손절] {info['name']}({code}) 수익률 {pnl:.2f}% (기준: {label})"
                    )
                    result = await kis.sell(code, info["qty"])
                    cp = await kis.price_raw(code)
                    await self._log(code, info["name"], "sell", info["qty"], cp, result["success"])
                    if result["success"]:
                        del self.bought[code]
                    continue

                # 익절 체크
                if pnl >= settings.take_profit_pct:
                    await self._msg(
                        f"[익절] {info['name']}({code}) 수익률 +{pnl:.2f}% ≥ +{settings.take_profit_pct}%"
                    )
                    result = await kis.sell(code, info["qty"])
                    cp = await kis.price_raw(code)
                    await self._log(code, info["name"], "sell", info["qty"], cp, result["success"])
                    if result["success"]:
                        del self.bought[code]

            except Exception as e:
                logger.error(f"Holdings check error ({code}): {e}")

    # 멀티팩터 매수 판단 
    async def _try_buy(self, sym: str, buy_amount: float) -> None:
        try:
            # 예측 데이터
            prediction = None
            if settings.use_prediction:
                try:
                    from service.predict import predict_stock
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
            await self._msg(
                f"[매수 시그널] {sym} 스코어={score:+.0f} ({', '.join(factor_strs)})"
            )

            # 동적 손절가 로그
            sp = result.get("stop_price")
            if sp:
                await self._msg(f"  → 구조적 손절가: {sp:,.0f}원")

            order = await kis.buy(sym, qty)
            if order["success"]:
                from service.kis import NAMES
                name = NAMES.get(sym, sym)
                self.bought[sym] = {
                    "avg_price": cp, "qty": qty, "name": name,
                    "stop_price": sp,
                }
            await self._log(sym, "", "buy", qty, cp, order["success"])

        except Exception as e:
            logger.error(f"Buy check error ({sym}): {e}")

    # 메인 루프 
    async def _run(self) -> None:
        try:
            total_cash = await kis.cash()
            items, evaluation = await kis.holdings()

            # 기존 보유 종목 등록
            for code, info in items.items():
                self.bought[code] = {
                    "avg_price": info["avg_price"],
                    "qty": info["qty"],
                    "name": info["name"],
                }

            buy_amount = total_cash * settings.buy_percent

            await self._msg("==== 보유잔고 ====")
            if items:
                for code, info in items.items():
                    name = info["name"]
                    qty = info["qty"]
                    avg = info.get("avg_price", 0)
                    cur = info.get("current_price", 0)
                    eval_amt = info.get("eval_amount", cur * qty)
                    pnl = info.get("profit_loss_percent", 0)
                    await self._msg(
                        f"  {name}({code}) {qty}주 | "
                        f"매입 {avg:,}원 → 현재 {cur:,}원 | "
                        f"평가 {eval_amt:,}원 ({pnl:+.2f}%)"
                    )
            else:
                await self._msg("  보유 종목 없음")
            await self._msg(f"주식 평가: {int(evaluation.get('scts_evlu_amt', '0')):,}원")
            await self._msg(f"평가 손익: {int(evaluation.get('evlu_pfls_smtl_amt', '0')):,}원")
            await self._msg(f"총 평가: {int(evaluation.get('tot_evlu_amt', '0')):,}원")
            await self._msg(f"매수 가능: {total_cash:,}원 (종목당 {buy_amount:,.0f}원)")
            await self._msg(f"전략: 멀티팩터 (매수>{settings.buy_score_threshold}점, 손절{settings.stop_loss_pct}%, 익절+{settings.take_profit_pct}%)")
            await self._msg("==================")

            soldout = False
            last_eval_min = -1

            while self.running:
                now = datetime.datetime.now()
                t_start = now.replace(hour=9, minute=5, second=0, microsecond=0)
                t_sell = now.replace(hour=15, minute=15, second=0, microsecond=0)
                t_exit = now.replace(hour=15, minute=20, second=0, microsecond=0)

                if now.weekday() >= 5:
                    await self._msg("주말이므로 프로그램을 종료합니다.")
                    break

                # 매매 시간대 (09:05 ~ 15:15)
                if t_start < now < t_sell:

                    # 손절/익절 체크 (30초마다)
                    if self.bought and now.second < 10:
                        await self._check_holdings()

                    # 매수 스캔 (5분마다, 동적 워치리스트)
                    if now.minute != last_eval_min and now.minute % 5 == 0:
                        last_eval_min = now.minute
                        from api.trade import symbols
                        scan_list = symbols()
                        for sym in scan_list:
                            if len(self.bought) >= settings.target_buy_count:
                                break
                            if sym in self.bought:
                                continue
                            await self._try_buy(sym, buy_amount)
                            await asyncio.sleep(1)

                    # 30분마다 잔고 갱신
                    if now.minute % 30 == 0 and now.second < 10:
                        items, _ = await kis.holdings()

                # 장 마감 전 일괄 매도
                if t_sell < now < t_exit and not soldout:
                    await self._msg("[장마감] 보유 종목 일괄 매도")
                    items, _ = await kis.holdings()
                    for code, info in items.items():
                        result = await kis.sell(code, info["qty"])
                        cp = info.get("current_price", 0)
                        await self._log(code, info["name"], "sell", info["qty"], cp, result["success"])
                    soldout = True
                    self.bought = {}

                if now > t_exit:
                    await self._msg("프로그램을 종료합니다.")
                    break

                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info("Bot cancelled")
        except Exception as e:
            await self._msg(f"[오류 발생] {e}")
            logger.exception("Bot error")
        finally:
            self.running = False

bot = Bot()
