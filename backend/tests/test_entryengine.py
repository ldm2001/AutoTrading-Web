import asyncio
import datetime
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
import service.ai.predict as predict_module
import service.trading.entryengine as entry_module
import service.trading.research as research_module
from service.ai.predict import Predictor
from service.infra.ttl_cache import TTLCache
from service.trading.entryengine import EntryEngine


# 알림 스텁
class NotifierStub:
    def __init__(self) -> None:
        self.msgs: list[str] = []

    async def msg(self, text: str) -> None:
        self.msgs.append(text)


# 포지션 스텁 — pos_result가 있으면 잔고 반영된 것으로 등록
class PositionStub:
    def __init__(self, pos_result: dict | None = None) -> None:
        self.bought: dict[str, dict] = {}
        self.pending_buys: set[str] = set()
        self.pending_stops: dict[str, float] = {}
        self._pos_result = pos_result

    async def pos(self, code: str, stop_price=None) -> dict | None:
        if self._pos_result is not None:
            self.bought[code] = self._pos_result
        return self._pos_result


# 기록 스텁
class JournalStub:
    def __init__(self) -> None:
        self.recs: list = []

    async def rec(self, *a) -> None:
        self.recs.append(a)


# broker 스텁
class BrokerStub:
    def __init__(self, indices=None) -> None:
        self.buys: list[tuple[str, int]] = []
        self._indices = indices

    async def purchase(self, code: str, qty: int) -> dict:
        self.buys.append((code, qty))
        return {"success": True}

    async def indices(self, stale: bool = True):
        if isinstance(self._indices, Exception):
            raise self._indices
        return self._indices or []


# 매수 시그널 평가 스텁
def buysig(score: float, price: int = 1000, stop_price=900):
    async def stub(_sym: str, _prediction: object) -> dict:
        return {
            "signal": "buy",
            "score": score,
            "price": price,
            "factors": [],
            "stop_price": stop_price,
        }
    return stub


# EntryEngine — 매수 진입 판단 + 시장 국면 게이트
class EntryEngineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._evaluate = entry_module.verdict
        self._wfin = entry_module.wfin
        self._regime = entry_module.mktregime
        entry_module.wfin = lambda *_a, **_k: None

    def tearDown(self) -> None:
        entry_module.verdict = self._evaluate
        entry_module.wfin = self._wfin
        entry_module.mktregime = self._regime

    # 매수 시그널 → 주문 + 포지션 등록 + 기록 + pending 해제
    async def testpurchase(self):
        entry_module.verdict = buysig(settings.buy_score_threshold + 10)
        pos = PositionStub(pos_result={"name": "삼성", "qty": 10, "avg_price": 1000})
        broker = BrokerStub()
        journal = JournalStub()
        e = EntryEngine(broker, pos, journal, NotifierStub(), {})
        await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [("005930", 10)])
        self.assertIn("005930", pos.bought)
        self.assertEqual(len(journal.recs), 1)
        self.assertNotIn("005930", pos.pending_buys)

    # 임계 미달 → 주문 없음
    async def testlowscore(self):
        entry_module.verdict = buysig(settings.buy_score_threshold - 1)
        broker = BrokerStub()
        e = EntryEngine(broker, PositionStub(), JournalStub(), NotifierStub(), {})
        await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [])

    # 예산 미달(수량 0) → 주문 없음
    async def testzeroqty(self):
        entry_module.verdict = buysig(settings.buy_score_threshold + 10, price=20_000)
        broker = BrokerStub()
        e = EntryEngine(broker, PositionStub(), JournalStub(), NotifierStub(), {})
        await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [])

    # 접수 후 잔고 미반영 → pending 유지 + 손절가 보관
    async def testpendflag(self):
        entry_module.verdict = buysig(settings.buy_score_threshold + 10)
        pos = PositionStub(pos_result=None)
        broker = BrokerStub()
        e = EntryEngine(broker, pos, JournalStub(), NotifierStub(), {})
        await e.ent("005930", 10_000)
        self.assertIn("005930", pos.pending_buys)
        self.assertEqual(pos.pending_stops.get("005930"), 900)

    # 보유 중 종목 재진입 차단
    async def testheld(self):
        entry_module.verdict = buysig(settings.buy_score_threshold + 10)
        pos = PositionStub()
        pos.bought["005930"] = {"qty": 1}
        broker = BrokerStub()
        e = EntryEngine(broker, pos, JournalStub(), NotifierStub(), {})
        await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [])

    # gate — risk_off면 차단 + 국면 전환 시 1회만 알림
    async def testriskoff(self):
        entry_module.mktregime = lambda _idx: {
            "state": "risk_off", "reason": "지수 급락", "allow_new_buys": False,
        }
        notifier = NotifierStub()
        e = EntryEngine(BrokerStub(indices=[]), PositionStub(), JournalStub(), notifier, {})
        self.assertFalse(await e.gate())
        self.assertFalse(await e.gate())
        self.assertEqual(len(notifier.msgs), 1)

    # gate — 지수 조회 실패 시 차단 + unknown 경보 (fail-closed) — 허용하면 시장을 못 본 채 매수
    async def testgatefault(self):
        notifier = NotifierStub()
        e = EntryEngine(BrokerStub(indices=RuntimeError("api down")), PositionStub(), JournalStub(), notifier, {})
        self.assertFalse(await e.gate())
        self.assertEqual(len(notifier.msgs), 1)
        self.assertIn("unknown", notifier.msgs[0])
        self.assertIn("api down", notifier.msgs[0])


# 캐시에 들어갈 예측 (2단계 형식 — 예측 {date, close, change_pct}, 지표 {mae, mae_zero, skill, n_val})
_PRED = {
    "predictions": [{"date": "2026-11-24", "close": 1000, "change_pct": 0.0}],
    "metrics": {"mae": 0.05, "mae_zero": 0.05, "skill": 0.0, "n_val": 20},
}


# M-03 — 플래그 True에서 봇은 학습을 기다리지 않는다 (캐시만 사용, 없으면 뒤에서 학습)
class EntryForecastTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._evaluate = entry_module.verdict
        self._wfin = entry_module.wfin
        self._flag = settings.use_prediction
        entry_module.wfin = lambda *_a, **_k: None
        self.seen: list = []

        # 관망 평가 — 받은 prediction만 기록
        async def hold(_sym: str, prediction: object) -> dict:
            self.seen.append(prediction)
            return {"signal": "hold", "score": 0, "price": 1000, "factors": []}

        entry_module.verdict = hold
        self.gate = threading.Event()
        self.fits: list[str] = []
        with mock.patch.object(TTLCache, "conn", lambda self: None):
            self.p = Predictor()

        # gate가 열릴 때까지 막히는 가짜 학습 (최대 2초)
        def trainer(symbol, *_a, **_k):
            self.fits.append(symbol)
            self.gate.wait(timeout=2)
            return _PRED

        self.p.training = trainer
        self._patches = [
            mock.patch.object(predict_module, "predictor", self.p),
            mock.patch.object(predict_module, "forecast", self.p.forecast),
        ]
        for p in self._patches:
            p.start()
        self.e = EntryEngine(BrokerStub(), PositionStub(), JournalStub(), NotifierStub(), {})

    # 남은 학습을 풀고 끝날 때까지 대기
    async def asyncTearDown(self) -> None:
        self.gate.set()
        for _ in range(200):
            if not self.p._inflight and not getattr(self.p, "_bg", None):
                break
            await asyncio.sleep(0.01)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self.p._executor.shutdown(wait=True)
        entry_module.verdict = self._evaluate
        entry_module.wfin = self._wfin
        settings.use_prediction = self._flag

    # 0.5초 안에 ent()가 끝나야 함 — 학습을 기다리면 실패
    async def quick(self) -> None:
        try:
            await asyncio.wait_for(self.e.ent("005930", 10_000), 0.5)
        except asyncio.TimeoutError:
            self.fail("ent()가 학습을 기다림")

    # 플래그 True·캐시 없음: training이 막혀 있어도 곧바로 끝나고 prediction=None, 학습은 뒤에서 1회
    async def testnowait(self):
        settings.use_prediction = True
        await self.quick()
        self.assertEqual(self.seen, [None])
        for _ in range(200):
            if self.fits:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.fits, ["005930"])

    # 플래그 True·캐시 있음: verdict가 캐시된 dict를 받고 재학습 없음
    async def testcached(self):
        settings.use_prediction = True
        self.gate.set()
        await self.p.forecast("005930")
        self.fits.clear()
        await self.quick()
        self.assertEqual(self.seen, [_PRED])
        self.assertEqual(self.fits, [])

    # 플래그 False: 예측 관련 호출이 전혀 없음
    async def testflagoff(self):
        settings.use_prediction = False
        calls: list[str] = []

        async def canned(sym: str) -> dict:
            calls.append(f"predict:{sym}")
            return _PRED

        with mock.patch.object(self.p, "cached", lambda s: calls.append(f"cached:{s}")), \
                mock.patch.object(self.p, "warmup", lambda s: calls.append(f"warm:{s}")), \
                mock.patch.object(self.p, "forecast", canned), \
                mock.patch.object(predict_module, "forecast", canned):
            await self.quick()
        self.assertEqual(calls, [])
        self.assertEqual(self.fits, [])
        self.assertEqual(self.seen, [None])

    # 예측 조회 실패는 경고 로그 + prediction=None으로 평가 계속 (조용히 넘기지 않음)
    async def testlookupfault(self):
        settings.use_prediction = True

        def broken(_s: str):
            raise RuntimeError("cache down")

        with mock.patch.object(self.p, "cached", broken), \
                self.assertLogs(entry_module.logger, level="WARNING") as logs:
            await self.quick()
        self.assertEqual(self.seen, [None])
        self.assertTrue(any("cache down" in m for m in logs.output), logs.output)


# 관망 평가 스텁 — 팩터 이름·점수·사유 포함
def holdsig(score: float = 12.5):
    async def stub(_sym: str, _prediction: object) -> dict:
        return {
            "signal": "hold",
            "score": score,
            "price": 1000,
            "factors": [
                {"name": "RSI", "score": 7.5, "max": 15, "reason": "RSI 35 (과매도 근접)"},
                {"name": "Direction", "score": 5.0, "max": 10, "reason": "방향 필터: 약상승 +1.2%"},
            ],
            "summary": "관망 (스코어 +12/100)",
            "stop_price": None,
        }
    return stub


# 고정 시각 — at을 바꿔 가며 평가
class ClockStub:
    def __init__(self, at: str) -> None:
        self.at = datetime.datetime.fromisoformat(at)

    def __call__(self) -> datetime.datetime:
        return self.at


# 주문 거부 broker 스텁
class RejectStub(BrokerStub):
    async def purchase(self, code: str, qty: int) -> dict:
        self.buys.append((code, qty))
        return {"success": False}


# 평가 기록 하루치 — 파일이 없으면 빈 목록
def lines(root: Path, day: str) -> list[dict]:
    path = Path(root) / f"{day}.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# E-03 — 평가 기록: 매수하지 않은 평가도 15분 구간마다 남기고, 주문 결과는 같은 eval_id로 잇는다
class EntryLedgerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._evaluate = entry_module.verdict
        self._wfin = entry_module.wfin
        self._flag = settings.use_prediction
        entry_module.wfin = lambda *_a, **_k: None
        settings.use_prediction = False
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "evals"
        self.clock = ClockStub("2026-11-24 10:01:00")

    def tearDown(self) -> None:
        entry_module.verdict = self._evaluate
        entry_module.wfin = self._wfin
        settings.use_prediction = self._flag
        self.tmp.cleanup()

    # 기록 폴더·시각을 바꾼 EntryEngine
    def engine(self, broker=None, pos=None, journal=None) -> EntryEngine:
        e = EntryEngine(broker or BrokerStub(), pos or PositionStub(), journal or JournalStub(), NotifierStub(), {})
        e.evals = self.root
        e.clock = self.clock
        return e

    # 같은 15분 구간의 두 번째 평가는 남기지 않음(종목별), 매수 신호는 매번, 구간이나 날짜가 바뀌면 다시 남김
    async def testslot(self):
        e = self.engine()
        entry_module.verdict = holdsig()
        await e.ent("005930", 10_000)
        self.clock.at = datetime.datetime(2026, 11, 24, 10, 14, 59)
        await e.ent("005930", 10_000)
        await e.ent("000660", 10_000)
        # 매수 신호 (예산 부족으로 주문 없음)
        entry_module.verdict = buysig(settings.buy_score_threshold + 10, price=20_000)
        await e.ent("005930", 10_000)
        await e.ent("005930", 10_000)
        entry_module.verdict = holdsig()
        self.clock.at = datetime.datetime(2026, 11, 24, 10, 15)
        await e.ent("005930", 10_000)
        # 다음 날 같은 시각 구간 (10:15) — 날짜가 다르면 새 구간
        self.clock.at = datetime.datetime(2026, 11, 25, 10, 16)
        await e.ent("005930", 10_000)
        rows = lines(self.root, "2026-11-24")
        self.assertEqual([(r["code"], r["signal"], r["time"]) for r in rows], [
            ("005930", "hold", "2026-11-24T10:01:00"),
            ("000660", "hold", "2026-11-24T10:14:59"),
            ("005930", "buy", "2026-11-24T10:14:59"),
            ("005930", "buy", "2026-11-24T10:14:59"),
            ("005930", "hold", "2026-11-24T10:15:00"),
        ])
        self.assertEqual([(r["code"], r["time"]) for r in lines(self.root, "2026-11-25")],
                         [("005930", "2026-11-25T10:16:00")])
        self.assertEqual(len({r["eval_id"] for r in rows}), len(rows))
        self.assertTrue(all(r["prediction"] is None and r["use_prediction"] is False for r in rows))

    # 매수하지 않은 평가도 한 줄 — 팩터 이름·점수·사유, 현재가·손절가, 예측 요약, 플래그 상태
    async def testhold(self):
        settings.use_prediction = True
        broker = BrokerStub()
        e = self.engine(broker)
        entry_module.verdict = holdsig()
        pred = {"predictions": [{"date": f"2026-11-{24 + k}", "close": 1000 + 10 * k} for k in range(5)],
                "base_close": 990, "base_date": "2026-11-21", "metrics": {"mae": 0.05}}
        with mock.patch.object(predict_module.predictor, "cached", lambda _s: pred):
            await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [])
        rows = lines(self.root, "2026-11-24")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertRegex(row.pop("eval_id"), r"^[0-9a-f]{12}$")
        self.assertEqual(row, {
            "kind": "eval",
            "time": "2026-11-24T10:01:00",
            "code": "005930",
            "score": 12.5,
            "signal": "hold",
            "factors": [
                {"name": "RSI", "score": 7.5, "reason": "RSI 35 (과매도 근접)"},
                {"name": "Direction", "score": 5.0, "reason": "방향 필터: 약상승 +1.2%"},
            ],
            "price": 1000,
            "stop_price": None,
            "prediction": {"base_close": 990, "base_date": "2026-11-21", "close5": 1040},
            "use_prediction": True,
        })

    # 주문하면 같은 eval_id로 결과 한 줄 (성공·거부 모두)
    async def testorder(self):
        entry_module.verdict = buysig(settings.buy_score_threshold + 10)
        await self.engine(BrokerStub(), PositionStub(pos_result={"name": "삼성", "qty": 10, "avg_price": 1000})) \
            .ent("005930", 10_000)
        await self.engine(RejectStub(), PositionStub()).ent("000660", 10_000)
        rows = lines(self.root, "2026-11-24")
        self.assertEqual([(r["kind"], r["code"]) for r in rows],
                         [("eval", "005930"), ("order", "005930"), ("eval", "000660"), ("order", "000660")])
        self.assertEqual(rows[1]["eval_id"], rows[0]["eval_id"])
        self.assertEqual(rows[3]["eval_id"], rows[2]["eval_id"])
        self.assertNotEqual(rows[0]["eval_id"], rows[2]["eval_id"])
        self.assertEqual(set(rows[1]), {"kind", "time", "code", "eval_id", "success", "qty"})
        self.assertEqual((rows[1]["success"], rows[1]["qty"]), (True, 10))
        self.assertEqual((rows[3]["success"], rows[3]["qty"]), (False, 10))
        self.assertEqual(rows[0]["stop_price"], 900)

    # 기록 폴더에 쓸 수 없어도 주문·포지션 등록·매매 기록은 그대로 (경고만, 같은 구간의 관망 평가는 경고 1회)
    async def testunwritable(self):
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("")
        broker = BrokerStub()
        pos = PositionStub(pos_result={"name": "삼성", "qty": 10, "avg_price": 1000})
        journal = JournalStub()
        e = self.engine(broker, pos, journal)
        e.evals = blocker / "evals"
        entry_module.verdict = buysig(settings.buy_score_threshold + 10)
        with self.assertLogs(entry_module.logger, level="WARNING") as logs:
            await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [("005930", 10)])
        self.assertIn("005930", pos.bought)
        self.assertEqual(len(journal.recs), 1)
        self.assertNotIn("005930", pos.pending_buys)
        self.assertEqual(len(logs.output), 2, logs.output)
        self.assertTrue(all("005930" in m and "WARNING" in m for m in logs.output), logs.output)
        entry_module.verdict = holdsig()
        with self.assertLogs(entry_module.logger, level="WARNING") as logs:
            await e.ent("000660", 10_000)
            await e.ent("000660", 10_000)
        self.assertEqual(len(logs.output), 1, logs.output)

    # 폴더를 주지 않으면 research 폴더 아래 evals/{날짜}.jsonl — 종목별 {code}.jsonl과 섞지 않음
    async def testdefault(self):
        base = Path(self.tmp.name) / "research"
        e = EntryEngine(BrokerStub(), PositionStub(), JournalStub(), NotifierStub(), {})
        e.clock = self.clock
        entry_module.verdict = holdsig()
        with mock.patch.object(research_module, "_ROOT", base):
            await e.ent("005930", 10_000)
        files = sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())
        self.assertEqual(files, ["evals/2026-11-24.jsonl"])


if __name__ == "__main__":
    unittest.main()
