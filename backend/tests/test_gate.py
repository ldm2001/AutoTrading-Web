import ast
import asyncio
import contextlib
import functools
import inspect
import sys
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

import api.stock as stock_api
import offline.injection as injection_module
import offline.walk as walk_module
import service.ai.predict as predict_module
import service.trading.backtest as backtest_module
import service.trading.entryengine as entry_module
import service.trading.strategy as strat
from config import settings
from service.ai.spec import VERSION
from service.trading.backtest import BacktestConfig, BacktestScorer, bt
from service.trading.entryengine import EntryEngine
from service.trading.strategy import W_PREDICT, Scorer
from tests.test_backtest import QuoteStub, market
from tests.test_offline_imports import sources

# 검증된 공용 모델 메타 (문이 읽는 필드만)
_META = {"validated": True, "version": VERSION}


# 지금 읽힌 공용 모델 묶음 흉내 — artifact() 반환 형식, 메타 필드만 바꿔 끼움
def poolstub(**meta) -> dict:
    return {"model": None, "stats": {}, "meta": {**_META, **meta}}


# 공용 모델 예측 모양 — 기준가 100에서 5일 동안 고르게 pct%, 기본은 검증된 공용 모델 출처·지금 사양
def pooled(pct: float = 4.0, day: str = "2026-01-16", **extra) -> dict:
    closes = [round(100 * (1 + pct / 100 * k / 5), 4) for k in range(1, 6)]
    row = {"predictions": [{"close": c} for c in closes], "base_close": 100, "base_date": day,
           "source": "pooled", "validated": True, "spec": VERSION}
    row.update(extra)
    return row


# 필드를 뺀 사본
def leftover(row: dict, *keys: str) -> dict:
    return {k: v for k, v in row.items() if k not in keys}


# 문 상태 — 플래그와 지금 읽힌 공용 모델(predictor.bundle)을 바꿔 끼움 (bundle=None이면 공용 모델 없음, fault면 확인이 예외)
def gateway(flag: bool = True, bundle: dict | None = None, fault: Exception | None = None) -> contextlib.ExitStack:
    def probe():
        if fault is not None:
            raise fault
        return bundle

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(settings, "use_prediction", flag))
    stack.enter_context(mock.patch.object(predict_module.predictor, "bundle", probe))
    return stack


# 3-6 — Direction이 0이 아니려면 플래그 True·출처 pooled·validated True·사양 일치·지금 읽힌 공용 모델 메타도 검증됨
class GateTest(unittest.TestCase):
    # 플래그 False면 검증된 공용 모델 예측도 0점 (기본값 False — 봇·추천·백테스트 기본 경로)
    def testflag(self):
        s = Scorer()
        with gateway(flag=False, bundle=poolstub()):
            self.assertEqual(s.pred(pooled(4.0), 100)[0], 0)
            self.assertEqual(s.pred(pooled(-4.0), 100)[0], 0)

    # 플래그·공용 모델이 열려 있어도 예측의 validated가 정확히 True가 아니면 0점 (False·None·"true"·1·필드 없음)
    def testvalidated(self):
        s = Scorer()
        with gateway(bundle=poolstub()):
            for value in (False, None, "true", 1):
                with self.subTest(validated=value):
                    self.assertEqual(s.pred(pooled(4.0, validated=value), 100)[0], 0)
            self.assertEqual(s.pred(leftover(pooled(4.0), "validated"), 100)[0], 0)

    # 출처가 정확히 "pooled"가 아니면 0점 — 종목별 즉석 학습(per_symbol)·다른 표기·빈 값·필드 없음
    def testsource(self):
        s = Scorer()
        with gateway(bundle=poolstub()):
            for value in ("per_symbol", "POOLED", "", None):
                with self.subTest(source=value):
                    self.assertEqual(s.pred(pooled(4.0, source=value), 100)[0], 0)
            self.assertEqual(s.pred(leftover(pooled(4.0), "source"), 100)[0], 0)

    # 예측 사양이 지금 코드의 VERSION과 다르면 0점
    def testspec(self):
        s = Scorer()
        with gateway(bundle=poolstub()):
            for value in ("v1-000000000000", VERSION + "x", None):
                with self.subTest(spec=value):
                    self.assertEqual(s.pred(pooled(4.0, spec=value), 100)[0], 0)
            self.assertEqual(s.pred(leftover(pooled(4.0), "spec"), 100)[0], 0)

    # 메타가 없으면 0점 — 옛 형식 예측(출처·검증·사양 필드 없음), 공용 모델 없음, 읽힌 공용 모델 메타가 검증 전·사양 다름·메타 없음
    def testmeta(self):
        s = Scorer()
        old = leftover(pooled(4.0), "source", "validated", "spec")
        with gateway(bundle=poolstub()):
            self.assertEqual(s.pred(old, 100)[0], 0)
        cases = {
            "공용 모델 없음": None,
            "메타 검증 전": poolstub(validated=False),
            "메타 검증 문자열": poolstub(validated="true"),
            "메타 검증 필드 없음": {"model": None, "stats": {}, "meta": {"version": VERSION}},
            "메타 사양 다름": poolstub(version="v1-000000000000"),
            "메타 없음": {"model": None, "stats": {}},
        }
        for label, bundle in cases.items():
            with self.subTest(label), gateway(bundle=bundle):
                self.assertEqual(s.pred(pooled(4.0), 100)[0], 0)

    # 공용 모델 확인이 예외를 내면 0점 + 경고 로그 (평가는 계속)
    def testfault(self):
        s = Scorer()
        with gateway(fault=RuntimeError("disk gone")), self.assertLogs(strat.logger, level="WARNING") as logs:
            self.assertEqual(s.pred(pooled(4.0), 100)[0], 0)
        self.assertTrue(any("disk gone" in m for m in logs.output), logs.output)

    # 앞 조건(플래그·출처·검증·사양)에서 닫히면 공용 모델을 읽지 않음 — 기본 경로에서 가중치 파일·torch 로드 없음
    def testorder(self):
        probes: list[int] = []

        def probe():
            probes.append(1)
            return poolstub()

        s = Scorer()
        with mock.patch.object(predict_module.predictor, "bundle", probe):
            with mock.patch.object(settings, "use_prediction", False):
                s.pred(pooled(4.0), 100)
            with mock.patch.object(settings, "use_prediction", True):
                for row in (pooled(4.0, validated=False), pooled(4.0, source="per_symbol"), pooled(4.0, spec=None)):
                    s.pred(row, 100)
                self.assertEqual(probes, [])
                s.pred(pooled(4.0), 100)
        self.assertEqual(probes, [1])

    # 모든 조건이 맞으면 규칙(labscore) 점수 그대로 — 상승 +10·약상승 +5·중립 0·약하락 −5·하락 −10, 기준가 없음 사유도 그대로
    def testopen(self):
        s = Scorer()
        with gateway(bundle=poolstub()):
            self.assertEqual(strat.predgate(pooled(4.0)), "")
            for pct, want in ((4.0, W_PREDICT), (2.0, W_PREDICT * 0.5), (0.0, 0), (-2.0, -W_PREDICT * 0.5),
                              (-4.0, -W_PREDICT)):
                with self.subTest(pct=pct):
                    self.assertEqual(s.pred(pooled(pct), 100)[0], want)
                    self.assertEqual(s.pred(pooled(pct), 100), s.labscore(pooled(pct), 100))
            self.assertEqual(s.pred(pooled(4.0, base_close=None), 100), (0, "예측 기준가 없음"))
        self.assertEqual(s.pred(None, 100), (0, "예측 데이터 없음"))

    # 봇(entryengine)·추천(api/stock)은 같은 Scorer.verdict, 백테스트는 BacktestScorer.val — 모두 같은 문 (검증 전 0점, 열리면 같은 점수)
    def testpaths(self):
        self.assertIs(entry_module.verdict, strat.verdict)
        self.assertIs(stock_api.verdict, strat.verdict)
        self.assertIs(strat.verdict.__func__, Scorer.verdict)
        daily, seq = market()
        i = 90
        day = seq[i]["time"].date().isoformat()
        past = [d for d in daily if d["date"] < day]
        price = seq[i]["close"]
        scorer = BacktestScorer()
        plain, _ = scorer.val(past, seq[:i], price)
        cases = (
            ("검증 전 예측", pooled(4.0, validated=False), poolstub(), 0),
            ("종목별 예측", pooled(4.0, source="per_symbol", validated=False), poolstub(), 0),
            ("공용 모델 검증 전", pooled(4.0), poolstub(validated=False), 0),
            ("공용 모델 없음", pooled(4.0), None, 0),
            ("열림", pooled(4.0), poolstub(), W_PREDICT),
        )
        for label, prediction, bundle, want in cases:
            with self.subTest(label), gateway(bundle=bundle):
                live = asyncio.run(Scorer(QuoteStub(past, price, seq[:i])).verdict("005930", prediction=prediction))
                factor = next(f for f in live["factors"] if f["name"] == "Direction")
                total, _ = scorer.val(past, seq[:i], price, prediction=prediction)
                self.assertEqual(factor["score"], want)
                self.assertAlmostEqual(total - plain, want, places=6)

    # 백테스트 bt도 같은 문 — 검증 전 예측 표·플래그 False는 주입 없음과 같은 결과, 열리면 진입이 바뀜
    def testbacktest(self):
        daily, seq = market()
        days = [d["date"] for d in daily]
        cfg = BacktestConfig(buy_threshold=25)
        good = {d: pooled(4.0, day=d) for d in days}
        raw = {d: pooled(4.0, day=d, validated=False) for d in days}
        with gateway(flag=False, bundle=poolstub()):
            plain = asdict(bt("005930", seq, daily, cfg))
            self.assertEqual(asdict(bt("005930", seq, daily, cfg, forecasts=good)), plain)
        with gateway(bundle=poolstub()):
            self.assertEqual(asdict(bt("005930", seq, daily, cfg, forecasts=raw)), plain)
            opened = bt("005930", seq, daily, cfg, forecasts=good)
        self.assertGreater(opened.total_trades, plain["total_trades"])

    # 하네스 전용 채점(LabScorer·labscore)은 문 없이 규칙대로 — 플래그 False·공용 모델 없음에서도 주입이 진입을 바꿈
    def testharness(self):
        daily, seq = market()
        days = [d["date"] for d in daily]
        cfg = BacktestConfig(buy_threshold=25)
        up = {d: pooled(4.0, day=d, source="per_symbol", validated=False) for d in days}
        down = {d: pooled(-4.0, day=d, source="per_symbol", validated=False) for d in days}
        with gateway(flag=False, bundle=None):
            plain = bt("005930", seq, daily, cfg)
            same = bt("005930", seq, daily, cfg, forecasts=up)
            base = backtest_module.playback(BacktestScorer(), "005930", seq, daily, cfg)
            lab = backtest_module.playback(injection_module.LabScorer(), "005930", seq, daily, cfg, forecasts=up)
            low = backtest_module.playback(injection_module.LabScorer(), "005930", seq, daily, cfg, forecasts=down)
            self.assertEqual(walk_module.direction(up[days[0]]), float(W_PREDICT))
            self.assertEqual(walk_module.direction(down[days[0]]), float(-W_PREDICT))
        self.assertEqual(asdict(same), asdict(plain))
        self.assertEqual(asdict(base), asdict(plain))
        self.assertGreater(lab.total_trades, plain.total_trades)
        self.assertLess(low.total_trades, plain.total_trades)

    # 라이브 경로에 우회 인자 없음 — 문을 끄는 매개변수를 두지 않음
    def testsignature(self):
        def names(fn) -> list[str]:
            return list(inspect.signature(fn).parameters)

        self.assertEqual(names(strat.predgate), ["prediction"])
        self.assertEqual(names(Scorer.pred), ["self", "prediction", "price"])
        self.assertEqual(names(Scorer.verdict), ["self", "code", "prediction", "fast"])
        self.assertEqual(names(BacktestScorer.val), ["self", "daily", "candles_15m", "price", "prediction"])
        self.assertEqual(names(bt), ["code", "candles_15m", "daily", "cfg", "span", "forecasts", "lag", "scores"])
        self.assertEqual(names(EntryEngine.ent), ["self", "sym", "buy_amount"])


# 서비스 쪽 소스(main·api·service)의 AST — 경로(백엔드 기준) → 트리
@functools.lru_cache(maxsize=1)
def trees() -> dict[str, ast.AST]:
    return {str(p.relative_to(_BACKEND)): ast.parse(p.read_text(encoding="utf-8"), filename=str(p)) for p in sources()}


# 이름을 가리키는 노드 — Name·Attribute·임포트 별칭·그 이름이 든 문자열 상수(getattr 등)
def mentions(tree: ast.AST, name: str) -> list[ast.AST]:
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            found.append(node)
        elif isinstance(node, ast.Attribute) and node.attr == name:
            found.append(node)
        elif isinstance(node, ast.alias) and name in (node.name.split(".")[-1], node.asname):
            found.append(node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and name in node.value:
            found.append(node)
    return found


# 서비스 소스 가운데 이름이 나오는 파일 → 노드 목록
def sightings(name: str) -> dict[str, list[ast.AST]]:
    return {path: found for path, tree in trees().items() if (found := mentions(tree, name))}


# 모듈 함수(owner=None) 또는 클래스 메서드 정의 노드
def method(tree: ast.AST, owner: str | None, name: str):
    scope = tree
    if owner is not None:
        scope = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner), None)
        if scope is None:
            return None
    return next((n for n in scope.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
                None)


# 3-6 — 하네스 전용 방법(문 없는 규칙 labscore, 채점기를 받는 playback, LabScorer)을 서비스 코드가 쓰지 않음
class BypassTest(unittest.TestCase):
    # labscore는 Scorer.pred 안(문 통과 뒤)에서 한 번만 — 다른 서비스 파일·다른 자리에는 없음
    def testlabscore(self):
        hits = sightings("labscore")
        self.assertEqual(set(hits), {"service/trading/strategy.py"})
        tree = trees()["service/trading/strategy.py"]
        self.assertIsNotNone(method(tree, "Scorer", "labscore"))
        inner = mentions(method(tree, "Scorer", "pred"), "labscore")
        self.assertEqual(len(inner), 1)
        self.assertEqual(len(hits["service/trading/strategy.py"]), 1)

    # playback은 bt 안에서 BacktestScorer()를 첫 인자로 한 번만 — 다른 서비스 파일·다른 자리에는 없음
    def testplayback(self):
        hits = sightings("playback")
        self.assertEqual(set(hits), {"service/trading/backtest.py"})
        tree = trees()["service/trading/backtest.py"]
        self.assertIsNotNone(method(tree, None, "playback"))
        calls = [n for n in ast.walk(method(tree, None, "bt"))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "playback"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(hits["service/trading/backtest.py"]), 1)
        first = calls[0].args[0]
        self.assertIsInstance(first, ast.Call)
        self.assertEqual((getattr(first.func, "id", None), first.args, first.keywords), ("BacktestScorer", [], []))

    # 하네스 전용 채점기 이름은 서비스 코드에 없음
    def testlab(self):
        for name in ("LabScorer", "LabRule"):
            with self.subTest(name=name):
                self.assertEqual(sightings(name), {})

    # 검사기가 하네스 쪽 사용을 찾아냄 (빈 검사로 초록이 되지 않게)
    def testscanner(self):
        self.assertGreater(len(trees()), 30)
        walk = ast.parse((_BACKEND / "offline" / "walk.py").read_text(encoding="utf-8"))
        injection = ast.parse((_BACKEND / "offline" / "injection.py").read_text(encoding="utf-8"))
        self.assertTrue(mentions(walk, "labscore"))
        self.assertTrue(mentions(injection, "labscore"))
        self.assertTrue(mentions(injection, "playback"))
        self.assertTrue(mentions(injection, "LabScorer"))


if __name__ == "__main__":
    unittest.main()
