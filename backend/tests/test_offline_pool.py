import ast
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import offline.pool as pool_module
import service.ai.predict as predict_module
from offline.pool import deposit, main, universe
from offline.rules import HOLDOUT_START
from offline.walk import rows
from service.ai.predict import META, WEIGHTS, artifact, network, panel
from service.ai.spec import FEATURES, PROFILE, SPEC, VERSION
from tests.test_spec import codebase

# validated를 true로 쓰는 JSON·문자열
_JSONTRUE = re.compile(r'"validated"\s*:\s*true', re.IGNORECASE)


# 합성 일봉 — [first, last] 영업일, 종목마다 다른 난수 걸음
def span(first: str, last: str, seed: int) -> pd.DataFrame:
    idx = pd.bdate_range(first, last, name="Date")
    n = len(idx)
    rng = np.random.default_rng(seed)
    close = 50_000 + np.cumsum(rng.normal(0, 500, n))
    return pd.DataFrame({"Open": close + rng.normal(0, 100, n), "High": close + 300, "Low": close - 300,
                         "Close": close, "Volume": rng.integers(1_000, 10_000, n).astype(float)}, index=idx)


# 가짜 일봉 공급자 — 요청을 기록하고, 요청 끝 날짜와 무관하게 2026-01-30까지 돌려줌 (로더가 잘라야 함)
class Source:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, code: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append((code, start, end))
        return span(start, "2026-01-30", int(code))


# 대상 종목 — NAMES에서 ETF를 뺀 목록 (추천 스캔 _SCAN_CODES와 같은 기준, offline은 api를 임포트하지 않음)
class UniverseTest(unittest.TestCase):
    def testscan(self):
        from api.stock import _SCAN_CODES
        from service.market.stock_universe import NAMES

        codes = universe()
        self.assertEqual(codes, _SCAN_CODES)
        self.assertGreater(len(codes), 20)
        for code in codes:
            self.assertFalse(any(tag in NAMES[code] for tag in ("KODEX", "TIGER", "ETF")), NAMES[code])


# CLI — 같은 날짜 컷으로 공용 학습, 요약에 윈도우 수와 날짜 수
class PoolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out, self.cache = root / "model", root / "cache"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def argv(self, *extra: str) -> list[str]:
        return ["--codes", "1,000002,000003", "--since", "2023-06-01", "--val-end", "2024-11-21",
                "--out", str(self.out), "--cache", str(self.cache), *extra]

    def outcome(self, argv: list[str], **kw) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, **kw)
        return code, out.getvalue(), err.getvalue()

    # 요청은 --since부터 --val-end까지(앞에 학습 창을 붙이지 않음), 요약의 윈도우·날짜 수 = 같은 일봉의 panel()
    def testsummary(self):
        src = Source()
        code, out, _ = self.outcome(self.argv(), reader=src, epochs=1)
        self.assertEqual(code, 0)
        self.assertEqual(src.calls, [(c, "2023-06-01", "2024-11-21") for c in ("000001", "000002", "000003")])
        frames = {c: span("2023-06-01", "2026-01-30", int(c)).loc[:"2024-11-21"] for c in ("000001", "000002", "000003")}
        p = panel(frames)
        for split, label in (("train", "학습"), ("val", "검증"), ("full", "전체")):
            c = p["counts"][split]
            self.assertIn(f"{label} 윈도우 {c['windows']:,} · 날짜 {c['dates']:,}", out)
        self.assertIn(f"컷 {p['cut'].date()}", out)
        self.assertIn("종목 3", out)

    # --train-end를 주면 그 날짜가 컷, --val-end가 최종 구간을 넘으면 그 전날까지만 요청
    def testbounds(self):
        src = Source()
        code, out, _ = self.outcome(self.argv("--train-end", "2024-09-02", "--val-end", "2026-01-15"),
                                    reader=src, epochs=1)
        self.assertEqual(code, 0)
        self.assertIn("컷 2024-09-02", out)
        self.assertTrue(all(c[2] < HOLDOUT_START for c in src.calls))

    # 3-3 저장 — weights.pt(state_dict만)·meta.json, 메타는 사양·버전·구간·종목 해시·윈도우/날짜 수·시드·통계·지표, validated는 false
    def testmeta(self):
        code, out, _ = self.outcome(self.argv("--train-end", "2024-09-02"), reader=Source(), epochs=1)
        self.assertEqual(code, 0)
        meta = json.loads((self.out / META).read_text(encoding="utf-8"))
        blob = (self.out / WEIGHTS).read_bytes()
        self.assertIs(meta["validated"], False)
        self.assertEqual(meta["version"], VERSION)
        self.assertEqual(meta["spec"], PROFILE)
        codes = ["000001", "000002", "000003"]
        self.assertEqual((meta["codes"], meta["count"]), (codes, 3))
        self.assertEqual(meta["codes_sha256"], hashlib.sha256(",".join(codes).encode()).hexdigest())
        self.assertIn("생존 편향", meta["universe"])
        self.assertEqual(meta["train_end"], "2024-09-02")
        self.assertEqual(meta["data"]["since"], "2023-06-01")
        self.assertEqual(meta["data"]["last"], "2024-11-21")
        self.assertEqual(meta["holdout_start"], HOLDOUT_START)
        frames = {c: span("2023-06-01", "2026-01-30", int(c)).loc[:"2024-11-21"] for c in codes}
        p = panel(frames, cut="2024-09-02")
        self.assertEqual(meta["counts"], p["counts"])
        self.assertLess(meta["spans"]["train"][1], "2024-09-02")
        self.assertGreaterEqual(meta["spans"]["val"][0], "2024-09-02")
        self.assertEqual(meta["spans"]["full"][1], "2024-11-21")
        for key in ("mu", "sd", "ysd"):
            np.testing.assert_allclose(meta["stats"][key], p["full"][key])
            np.testing.assert_allclose(meta["stats_trial"][key], p["stats"][key])
        self.assertEqual(meta["seed"], 0)
        self.assertEqual(meta["epochs"], {"max": 1, "chosen": 1})
        self.assertEqual(set(meta["metrics"]), {"mae", "mae_zero", "skill", "n_val"})
        self.assertEqual(meta["metrics"]["n_val"], p["counts"]["val"]["windows"])
        self.assertEqual(meta["weights_sha256"], hashlib.sha256(blob).hexdigest())
        state = torch.load(io.BytesIO(blob), weights_only=True)
        self.assertTrue(all(isinstance(v, torch.Tensor) for v in state.values()))
        got = artifact(self.out)
        self.assertIsNotNone(got)
        np.testing.assert_allclose(got["stats"]["mu"], p["full"]["mu"])
        self.assertIn("validated false", out)
        self.assertEqual(sorted(f.name for f in self.out.iterdir()), sorted([META, WEIGHTS]))

    # 이미 모델이 있는 폴더에는 쓰지 않음 (사용자가 validated를 고친 모델을 덮어쓰지 않게) — 공급자도 부르지 않음
    def testrefuse(self):
        self.assertEqual(self.outcome(self.argv(), reader=Source(), epochs=1)[0], 0)
        before = {f.name: f.read_bytes() for f in self.out.iterdir()}
        src = Source()
        code, _, err = self.outcome(self.argv(), reader=src, epochs=1)
        self.assertEqual(code, 2)
        self.assertIn("이미", err)
        self.assertEqual(src.calls, [])
        self.assertEqual({f.name: f.read_bytes() for f in self.out.iterdir()}, before)

    # 메타 쓰기는 항상 false — 넘긴 메타에 true가 있어도 덮어씀
    def testfalse(self):
        model = network(0, len(PROFILE["features"]), PROFILE["horizon"], PROFILE["seq"])
        meta = deposit(self.out, model, {"validated": True, "version": VERSION})
        self.assertIs(meta["validated"], False)
        self.assertIs(json.loads((self.out / META).read_text(encoding="utf-8"))["validated"], False)


# 부르면 안 되는 학습 함수 자리
def tripwire(*args, **kwargs):
    raise AssertionError("학습 시작됨")


# 학습 기록 — 진짜 session()에 넘기기 전에 종목별 첫·마지막 일봉 날짜를 남김
class Spy:
    def __init__(self, fault: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.fault = fault
        self.real = pool_module.session

    def __call__(self, frames, **kw):
        self.calls.append({"first": {c: df.index.min() for c, df in frames.items()},
                           "last": {c: df.index.max() for c, df in frames.items()}, **kw})
        if self.fault:
            raise self.fault
        return self.real(frames, **kw)


# 3종목 합성 일봉 (2023-06-01 ~ 2025-06-30)
def basket() -> dict[str, pd.DataFrame]:
    return {c: span("2023-06-01", "2025-06-30", int(c)) for c in ("000001", "000002", "000003")}


# 평가 컷 4개 (2025-03-03·03-10·03-17·03-24) — 2025-03-03 ~ 04-04, 5거래일 간격
_EVAL = ("2025-03-03", "2025-04-04")
_CUTS = ["2025-03-03", "2025-03-10", "2025-03-17", "2025-03-24"]


# 3-4 공용 워크포워드 — 컷 every개마다(0이면 첫 컷 한 번) [시작, 컷] 일봉으로 재학습, 사이 컷은 그 모델로 추론만
class RetrainerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frames = basket()

    def walk(self, every: int, frames=None, spy=None):
        spy = spy or Spy()
        with mock.patch.object(pool_module, "session", spy):
            core = pool_module.Retrainer(frames or self.frames, every=every, epochs=1)
            table, skipped = rows(frames or self.frames, *_EVAL, core=core)
        return core, table, skipped, spy

    # every=2 — 0번·2번 컷에서 학습, 학습 일봉은 그 컷까지(뒤 행 없음), 같은 결과 표
    def testinterval(self):
        core, table, skipped, spy = self.walk(2)
        self.assertEqual(skipped, [])
        self.assertEqual(sorted({r["base_date"] for r in table}), _CUTS)
        self.assertEqual(len(table), 12)
        self.assertEqual([set(c["last"].values()) for c in spy.calls],
                         [{pd.Timestamp(_CUTS[0])}, {pd.Timestamp(_CUTS[2])}])
        for call in spy.calls:
            self.assertEqual(set(call["first"].values()), {pd.Timestamp("2023-06-01")})
            self.assertEqual((call["epochs"], call["seed"]), (1, pool_module.SEED))
        self.assertEqual([t["cut"] for t in core.trained], [_CUTS[0], _CUTS[2]])
        for t in core.trained:
            self.assertEqual(set(t["metrics"]), {"mae", "mae_zero", "skill", "n_val"})
            self.assertEqual(t["count"], 3)

    # every=0 — 첫 컷에서 한 번만 학습하고 4개 컷 모두 그 모델로 추론
    def testonce(self):
        core, table, skipped, spy = self.walk(0)
        self.assertEqual(skipped, [])
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual([t["cut"] for t in core.trained], [_CUTS[0]])
        self.assertEqual({r["val_mae"] for r in table}, {core.trained[0]["metrics"]["mae"]})

    # 미래 누수 없음 — 첫 컷 뒤 일봉을 바꿔도 첫 컷 예측은 그대로 (학습·입력 모두 컷까지만)
    def testleak(self):
        _, first, _, _ = self.walk(0)
        moved = {c: df.copy() for c, df in self.frames.items()}
        for df in moved.values():
            df.loc[df.index > pd.Timestamp(_CUTS[0]), ["Open", "High", "Low", "Close"]] *= 1.5
        _, again, _, _ = self.walk(0, frames=moved)
        cols = ["code", "base_close", "pred1", "pred2", "pred3", "pred4", "pred5"]
        pick = lambda t: [{k: r[k] for k in cols} for r in t if r["base_date"] == _CUTS[0]]
        self.assertEqual(pick(first), pick(again))
        self.assertNotEqual([r["actual_close"] for r in first if r["base_date"] == _CUTS[0]],
                            [r["actual_close"] for r in again if r["base_date"] == _CUTS[0]])

    # 결과 형식 — 종목별 core()와 같은 키 + source pooled·validated false·spec 버전, 지표는 그 학습의 검증 지표
    def testformat(self):
        core = pool_module.Retrainer(self.frames, every=0, epochs=1)
        window = self.frames["000002"].loc[:_CUTS[0]].iloc[-250:]
        out = core(window.copy(), seed=123, label="000002")
        self.assertEqual(set(out), {"predictions", "metrics", "base_close", "base_date", "source", "validated", "spec"})
        self.assertEqual((out["source"], out["validated"], out["spec"]), ("pooled", False, VERSION))
        self.assertEqual(out["base_date"], _CUTS[0])
        self.assertEqual(out["metrics"], core.trained[0]["metrics"])
        self.assertEqual(len(out["predictions"]), SPEC["horizon"])

    # 학습 실패 — 그 컷은 학습을 한 번만 시도하고 모든 종목이 사유와 함께 건너뜀, 다음 재학습 컷까지 이어짐
    def testfault(self):
        spy = Spy(fault=ValueError("학습 윈도우 부족"))
        core, table, skipped, _ = self.walk(0, spy=spy)
        self.assertEqual(table, [])
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(len(skipped), 12)
        self.assertTrue(all("공용 학습 실패" in s["reason"] and "학습 윈도우 부족" in s["reason"] for s in skipped))
        self.assertEqual(core.trained, [])


# 저장 모델 — deposit()으로 쓴 무작위 가중치·단위 통계 (data는 메타의 일봉 구간, None이면 빼고 씀)
def stored(folder: Path, data: dict | None) -> None:
    model = network(0, len(FEATURES), SPEC["horizon"], SPEC["seq"])
    meta = {"version": VERSION, "spec": PROFILE,
            "stats": {"mu": [0.0] * len(FEATURES), "sd": [1.0] * len(FEATURES), "ysd": [0.02] * SPEC["horizon"]},
            "metrics": {"mae": 1.0, "mae_zero": 1.1, "skill": 0.0909, "n_val": 50}}
    if data is not None:
        meta["data"] = data
    deposit(folder, model, meta)


# 3-4 저장된 공용 모델 평가 — 학습 없이 추론만, 모델 일봉 끝(meta data.last)보다 앞 컷은 학습 구간이라 거부
class CheckpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.frames = basket()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # 일봉 끝 2025-03-17 — 03-03·03-10 컷은 거부(사유), 03-17·03-24는 추론, 학습 함수를 부르지 않음
    def testguard(self):
        stored(self.root, {"since": "2023-06-01", "last": "2025-03-17"})
        core = pool_module.Checkpoint(self.root)
        with mock.patch.object(predict_module, "trial", tripwire), \
             mock.patch.object(predict_module, "fullmodel", tripwire), \
             mock.patch.object(predict_module, "network", tripwire), \
             mock.patch.object(pool_module, "session", tripwire):
            table, skipped = rows(self.frames, *_EVAL, core=core)
        self.assertEqual(sorted({r["base_date"] for r in table}), _CUTS[2:])
        self.assertEqual(sorted({s["base_date"] for s in skipped}), _CUTS[:2])
        self.assertTrue(all("학습 구간" in s["reason"] for s in skipped))
        self.assertEqual({r["val_mae"] for r in table}, {1.0})
        out = core(self.frames["000001"].loc[:_CUTS[3]].copy(), seed=0, label="000001")
        self.assertEqual((out["source"], out["validated"]), ("pooled", False))
        self.assertEqual(core.meta["data"]["last"], "2025-03-17")

    # 읽을 수 없는 모델(폴더 없음·사양 불일치)과 일봉 끝이 없는 메타는 만들 때 ValueError
    def testrefuse(self):
        with self.assertRaises(ValueError):
            pool_module.Checkpoint(self.root / "none")
        stored(self.root / "bare", None)
        with self.assertRaises(ValueError):
            pool_module.Checkpoint(self.root / "bare")
        stored(self.root / "spec", {"last": "2025-03-17"})
        meta = json.loads((self.root / "spec" / META).read_text(encoding="utf-8"))
        meta["version"] = "v1-000000000000"
        (self.root / "spec" / META).write_text(json.dumps(meta), encoding="utf-8")
        with self.assertRaises(ValueError):
            pool_module.Checkpoint(self.root / "spec")


# 상수 True
def truth(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


# 문자열 상수 "validated"
def flagkey(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == "validated"


# 대입 대상이 validated 자리 — m["validated"] · obj.validated · validated
def slot(node) -> bool:
    return ((isinstance(node, ast.Subscript) and flagkey(node.slice))
            or (isinstance(node, ast.Attribute) and node.attr == "validated")
            or (isinstance(node, ast.Name) and node.id == "validated"))


# validated를 true로 쓰는 자리의 줄 번호 — dict 값·대입·키워드 인자·("validated", True) 인자(setdefault·setattr·get 기본값)·JSON 문자열
def approvals(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            hit = any(flagkey(k) and truth(v) for k, v in zip(node.keys, node.values))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            hit = truth(node.value) and any(slot(t) for t in targets)
        elif isinstance(node, ast.keyword):
            hit = node.arg == "validated" and truth(node.value)
        elif isinstance(node, ast.Call):
            hit = any(flagkey(a) and truth(b) for a, b in zip(node.args, node.args[1:]))
        else:
            hit = isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(_JSONTRUE.search(node.value))
        if hit:
            lines.append(node.lineno)
    return sorted(lines)


# 3-3·3-9 — 코드(main·api·service·offline) 어디에도 validated를 true로 쓰는 곳이 없음 (사용자가 최종 판정 뒤 meta.json을 직접 고침)
class ApprovalTest(unittest.TestCase):
    # 저장소 코드 전체에서 찾은 자리 없음 — 테스트(tests/)는 임시 폴더·가짜 묶음에만 true를 씀
    def testwriter(self):
        root = Path(__file__).resolve().parents[1]
        files = codebase()
        found = {}
        for path in files:
            lines = approvals(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            if lines:
                found[str(path.relative_to(root))] = lines
        self.assertGreater(len(files), 40)
        self.assertEqual(found, {})

    # 검사기 자체 — 심은 true 쓰기는 모두 찾고, false 쓰기·is True 비교·메타 값 전달은 건너뜀
    def testscanner(self):
        planted = {
            "dict": 'm = {"validated": True}',
            "subscript": 'm["validated"] = True',
            "keyword": "dict(validated=True)",
            "attribute": "m.validated = True",
            "name": "validated = True",
            "annotated": "validated: bool = True",
            "setdefault": 'm.setdefault("validated", True)',
            "setattr": 'setattr(m, "validated", True)',
            "default": 'ok = m.get("validated", True)',
            "json": """text = '{"validated": true}'""",
        }
        for label, code in planted.items():
            with self.subTest(case=label):
                self.assertEqual(approvals(ast.parse(code)), [1])
        allowed = ('m = {"validated": False}', 'm = {**m, "validated": False}', 'm["validated"] = False',
                   'ok = m.get("validated") is True', 'bad = m.get("validated") is not True',
                   'out = {"validated": meta.get("validated") is True}', "deposit(out, model, validated=False)",
                   """text = f"validated {'true' if ok else 'false'}" """)
        for code in allowed:
            with self.subTest(code=code):
                self.assertEqual(approvals(ast.parse(code)), [])


if __name__ == "__main__":
    unittest.main()
