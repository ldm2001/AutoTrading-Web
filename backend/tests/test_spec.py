import ast
import contextlib
import copy
import importlib
import inspect
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

import offline.pool as pool_module
import offline.walk as walk_module
import service.ai.predict as predict_module
import service.ai.spec as spec_module
from service.ai.spec import FEATURES, SPEC, VERSION
from tests.test_offline_imports import sources
from tests.test_pooled import series
from tests.test_predict import predrig

# 호출을 기록하는 피처·윈도우·예측 입력 함수
_TRACED = ("features", "windows", "latest")
# service/ai/predict.py에만 있어야 하는 정의 — 피처·윈도우·분할·표준화·데이터셋·예측 입력·모델·추론
_SHARED = ("features", "windows", "holdout", "moments", "zscore", "samples", "panel", "latest", "projection",
           "network", "inference", "StockTransformer")
# service/ai/spec.py에만 있어야 하는 모듈 상수
_CONSTANTS = ("SPEC", "FEATURES", "WARM", "REVISION", "PROFILE", "VERSION")


# 3-1 — 사양 하나: 피처·입력 길이·예측 기간·모델 크기·표준화 방식을 한 dict와 버전 문자열로
class SpecTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = importlib.import_module("service.ai.spec")

    # predict.py는 사양 모듈의 값을 그대로 씀 (사본·재정의 없음)
    def testsource(self):
        for name in ("SPEC", "FEATURES", "WARM", "PROFILE", "VERSION"):
            with self.subTest(name=name):
                self.assertIs(getattr(predict_module, name), getattr(self.spec, name))

    # 사양 dict — 피처 목록·앞 버림 행·타깃·표준화 방식과 2단계 설정값 전부
    def testprofile(self):
        p = self.spec.PROFILE
        self.assertEqual(p["features"], list(self.spec.FEATURES))
        self.assertEqual(p["warm"], self.spec.WARM)
        self.assertEqual(p["revision"], self.spec.REVISION)
        self.assertIn("target", p)
        self.assertIn("scale", p)
        for k, v in self.spec.SPEC.items():
            with self.subTest(key=k):
                self.assertEqual(p[k], v)

    # 버전 = 사양의 서명 — 키 순서·JSON 왕복과 무관, 값이 하나라도 다르면 다름
    def testversion(self):
        p = self.spec.PROFILE
        sig = self.spec.signature
        self.assertEqual(self.spec.VERSION, sig(p))
        self.assertEqual(sig(dict(reversed(list(p.items())))), self.spec.VERSION)
        self.assertEqual(json.loads(json.dumps(p)), p)
        self.assertEqual(sig(json.loads(json.dumps(p))), self.spec.VERSION)
        changes = {"seq": 21, "horizon": 4, "d_model": 64, "revision": p["revision"] + 1,
                   "features": list(reversed(p["features"])), "scale": "minmax"}
        for k, v in changes.items():
            with self.subTest(key=k):
                alt = copy.deepcopy(p)
                alt[k] = v
                self.assertNotEqual(sig(alt), self.spec.VERSION)


# 검사 대상 소스 — main.py, api/, service/, offline/ (tests·venv 제외)
def codebase() -> list[Path]:
    return sources() + sorted((_BACKEND / "offline").rglob("*.py"))


# predict 모듈의 피처·윈도우·예측 입력 함수를 값은 그대로 두고 호출만 기록하는 감싸기로 바꿈 → {이름: mock}
@contextlib.contextmanager
def recorders():
    with contextlib.ExitStack() as stack:
        yield {name: stack.enter_context(mock.patch.object(predict_module, name, wraps=getattr(predict_module, name)))
               for name in _TRACED}


# 3-1 — 구현이 하나: 종목별 core(), 공용 학습(offline.pool), 서빙·하네스 추론(projection)이 같은 피처·윈도우 함수와 같은 사양을 씀
class ShareTest(unittest.TestCase):
    # 세 경로가 predict 모듈의 같은 함수를 같은 seq·horizon으로 부름 — 공용 학습은 종목마다 한 번, 서빙은 공용 모델이 있으면 추론만
    def testcallers(self):
        signs = {name: inspect.signature(getattr(predict_module, name)) for name in _TRACED}
        p = predrig()
        self.addCleanup(p._executor.shutdown, wait=True)
        df = series(220)
        frames = {"000001": series(220, seed=1), "000002": series(230, seed=2)}
        bundle = {"model": predict_module.network(0, len(FEATURES), SPEC["horizon"], SPEC["seq"]).eval(),
                  "stats": {"mu": np.zeros(len(FEATURES)), "sd": np.ones(len(FEATURES)),
                            "ysd": np.full(SPEC["horizon"], 0.02)},
                  "meta": {"version": VERSION, "validated": False}}

        # 서빙 — 일봉 조회·공용 모델은 가짜, training()이 projection 경로로 감
        def serving() -> dict:
            with mock.patch.object(p, "raw", lambda symbol: df.copy()), mock.patch.object(p, "bundle", lambda: bundle):
                return p.training("005930")

        cases = {
            "core": (lambda: p.core(df.copy(), seed=0, epochs=1), "per_symbol", {"features": 1, "windows": 1, "latest": 1}),
            "pooled": (lambda: pool_module.session(frames, epochs=1), None, {"features": 2, "windows": 2}),
            "serving": (serving, "pooled", {"features": 1, "latest": 1}),
        }
        for label, (path, source, want) in cases.items():
            with self.subTest(path=label), recorders() as rec:
                out = path()
                if source:
                    self.assertEqual(out["source"], source)
                self.assertEqual({name: rec[name].call_count for name in want}, want)
                for name, m in rec.items():
                    for call in m.call_args_list:
                        bound = signs[name].bind(*call.args, **call.kwargs)
                        bound.apply_defaults()
                        for key in ("seq", "horizon"):
                            if key in bound.arguments:
                                self.assertEqual(bound.arguments[key], SPEC[key], f"{name} {key}")

    # 오프라인 학습·하네스가 쓰는 이름은 predict·spec 모듈의 바로 그 객체 — 종목별 하네스는 배포 core()를 그대로 부름
    def testidentity(self):
        for name in ("panel", "projection", "trial", "fullmodel", "inference", "scorecard", "artifact"):
            with self.subTest(name=name):
                self.assertIs(getattr(pool_module, name), getattr(predict_module, name))
        for name in ("SPEC", "PROFILE", "VERSION"):
            with self.subTest(name=name):
                self.assertIs(getattr(pool_module, name), getattr(spec_module, name))
        self.assertEqual(walk_module.HORIZON, SPEC["horizon"])
        fake = mock.Mock(return_value={"source": "per_symbol"})
        with mock.patch.object(predict_module.predictor, "core", fake):
            out = walk_module.deployment("window", 3, "005930")
        self.assertEqual(out, {"source": "per_symbol"})
        fake.assert_called_once_with("window", seed=3, label="005930")

    # 정의 위치 — 피처·윈도우 등은 service/ai/predict.py에만, 사양 상수는 service/ai/spec.py에만 (중첩 함수·메서드 포함)
    def testsingle(self):
        found: dict[str, set[str]] = {name: set() for name in _SHARED + _CONSTANTS}
        for path in codebase():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = str(path.relative_to(_BACKEND))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in _SHARED:
                    found[node.name].add(rel)
            for node in tree.body:
                targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
                found.update({t.id: found[t.id] | {rel} for t in targets
                              if isinstance(t, ast.Name) and t.id in _CONSTANTS})
        self.assertGreater(len(codebase()), 40)
        for name in _SHARED:
            with self.subTest(name=name):
                self.assertEqual(found[name], {"service/ai/predict.py"})
        for name in _CONSTANTS:
            with self.subTest(name=name):
                self.assertEqual(found[name], {"service/ai/spec.py"})


if __name__ == "__main__":
    unittest.main()
