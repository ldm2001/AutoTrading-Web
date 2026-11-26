import asyncio
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.ai.predict as predict_module
from config import Settings, settings
from offline.pool import deposit
from service.ai.spec import FEATURES, PROFILE, SPEC, VERSION
from service.trading import research
from tests.test_predict import predrig

# 피클 실행 흔적 — weights_only 없이 읽으면 alarm()이 불림
_HITS: list[str] = []


# 피클이 실행하는 함수 (weights_only=True면 불리지 않아야 함)
def alarm() -> str:
    _HITS.append("tripped")
    return "tripped"


# 읽는 순간 alarm()을 부르는 피클 객체
class Tripwire:
    def __reduce__(self):
        return (alarm, ())


# 합성 일봉 n행 (영업일, 마지막 날 end, 종목마다 다른 난수 걸음 — seed 7이면 test_predict.frame과 같음)
def series(n: int, end: str = "2026-11-20", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 50_000 + np.cumsum(rng.normal(0, 500, n))
    return pd.DataFrame(
        {
            "Open":   close + rng.normal(0, 100, n),
            "High":   close + 300,
            "Low":    close - 300,
            "Close":  close,
            "Volume": rng.integers(1_000, 10_000, n).astype(float),
        },
        index=pd.bdate_range(end=end, periods=n),
    )


# 컷 날짜부터의 행을 바꾼 사본 — 가격 1.5배, 거래량 7배
def shifted(df: pd.DataFrame, cut) -> pd.DataFrame:
    out = df.copy()
    rows = out.index >= pd.Timestamp(cut)
    for col, k in (("Open", 1.5), ("High", 1.5), ("Low", 1.5), ("Close", 1.5), ("Volume", 7.0)):
        out.loc[rows, col] *= k
    return out


# 달력이 서로 다른 세 종목 — B는 한 주 먼저 끝나고, C는 중간 10거래일이 빠짐(거래정지)
def trio() -> dict[str, pd.DataFrame]:
    c = series(250, seed=3)
    c = c.drop(c.index[100:110])
    return {"000003": c, "000001": series(260, seed=1), "000002": series(200, end="2026-11-13", seed=2)}


# 3-2 — 공용 데이터셋: 날짜 컷 하나(모든 종목 공통), 학습 = 타깃 마지막 날 < 컷, 검증 = 기준일 ≥ 컷, 그 사이 purge
class PanelTest(unittest.TestCase):
    # 종목이 하나면 2단계 samples()와 같은 분할·통계·텐서 (텐서는 학습 데이터셋과 같은 float32)
    def testsingle(self):
        df = series(220)
        p = predict_module.panel({"005930": df})
        s = predict_module.samples(df)
        self.assertEqual(p["cut"], s["cut"])
        for key in ("xt", "yt", "xv", "yv"):
            with self.subTest(key=key):
                self.assertEqual(p[key].dtype, np.float32)
                np.testing.assert_array_equal(p[key], s[key].astype(np.float32))
        for key in ("mu", "sd", "ysd"):
            np.testing.assert_array_equal(p["stats"][key], s["stats"][key])
        np.testing.assert_array_equal(p["yraw"], s["y"][s["v0"]:])
        self.assertEqual(p["counts"]["train"]["windows"], s["ntr"])
        self.assertEqual(p["counts"]["val"]["windows"], len(s["X"]) - s["v0"])
        self.assertEqual(p["counts"]["full"]["windows"], len(s["X"]))
        self.assertEqual(p["codes"], ["005930"])

    # 컷은 날짜 하나 — 전 종목 기준일 날짜의 뒤쪽 15%(최소 20일), 학습 타깃은 모두 컷 전에 끝나고 검증 기준일은 모두 컷 이후
    def testcut(self):
        p = predict_module.panel(trio())
        tags, cut = p["tags"], p["cut"]
        days = sorted(set(tags["full"]["base"]))
        self.assertEqual(cut, days[-max(int(len(days) * 0.15), 20)])
        self.assertTrue((tags["train"]["tail"] < cut).all())
        self.assertTrue((tags["val"]["base"] >= cut).all())
        for code in ("000001", "000002", "000003"):
            with self.subTest(code=code):
                self.assertIn(code, set(tags["train"]["code"]))
                self.assertIn(code, set(tags["val"]["code"]))
        # 컷을 주면 그 날짜 그대로 (종목마다 따로 정하지 않음)
        fixed = predict_module.panel(trio(), cut="2026-09-01")
        self.assertEqual(fixed["cut"], pd.Timestamp("2026-09-01"))
        self.assertTrue((fixed["tags"]["train"]["tail"] < fixed["cut"]).all())
        self.assertTrue((fixed["tags"]["val"]["base"] >= fixed["cut"]).all())

    # purge — 기준일 < 컷 ≤ 타깃 마지막 날인 윈도우는 학습·검증 어디에도 없음, 컷 주변 거래가 이어진 종목은 정확히 5개
    def testpurge(self):
        p = predict_module.panel(trio())
        tags, cut = p["tags"], p["cut"]
        for code in ("000001", "000002", "000003"):
            with self.subTest(code=code):
                full = tags["full"][tags["full"]["code"] == code]
                train = tags["train"][tags["train"]["code"] == code]
                val = tags["val"][tags["val"]["code"] == code]
                gap = full[(full["base"] < cut) & (full["tail"] >= cut)]
                self.assertEqual(len(full), len(train) + len(val) + len(gap))
                self.assertEqual(len(gap), 5)
                self.assertTrue(set(gap["base"]).isdisjoint(set(train["base"]) | set(val["base"])))

    # 미래 누수 없음 — 컷 이후 행을 바꿔도 학습 텐서·통계는 그대로, 검증 입력만 달라짐 (컷을 주든 날짜 규칙이든)
    def testleak(self):
        base = predict_module.panel(trio())
        cut = base["cut"]
        moved = {code: shifted(df, cut) for code, df in trio().items()}
        for label, arg in (("rule", None), ("fixed", cut)):
            with self.subTest(cut=label):
                alt = predict_module.panel(moved, cut=arg)
                self.assertEqual(alt["cut"], cut)
                for key in ("xt", "yt"):
                    np.testing.assert_array_equal(base[key], alt[key])
                for key in ("mu", "sd", "ysd"):
                    np.testing.assert_array_equal(base["stats"][key], alt["stats"][key])
                self.assertFalse(np.array_equal(base["xv"], alt["xv"]))

    # 통계 — 학습 윈도우의 입력 행·타깃을 전 종목에서 모아 한 번 (종목별 통계 아님), full은 전체 윈도우(재학습·서빙용)
    def testscale(self):
        frames = trio()
        p = predict_module.panel(frames)
        cut = p["cut"]
        rows, ys, allrows, ally, xs = [], [], [], [], []
        for code in sorted(frames):
            feats = predict_module.features(frames[code])
            X, y, ends = predict_module.windows(feats, frames[code]["Close"])
            keep = np.asarray(feats.index[ends + 5] < cut)
            values = feats.to_numpy(dtype=float)
            rows.append(values[: ends[keep][-1] + 1])
            ys.append(y[keep])
            xs.append(X[keep])
            allrows.append(values[: ends[-1] + 1])
            ally.append(y)
        want = predict_module.moments(np.concatenate(rows), np.concatenate(ys))
        whole = predict_module.moments(np.concatenate(allrows), np.concatenate(ally))
        for key in ("mu", "sd", "ysd"):
            np.testing.assert_allclose(p["stats"][key], want[key])
            np.testing.assert_allclose(p["full"][key], whole[key])
        np.testing.assert_allclose(p["xt"], predict_module.zscore(np.concatenate(xs), want), rtol=1e-6, atol=1e-6)
        np.testing.assert_allclose(p["yt"], np.concatenate(ys) / want["ysd"], rtol=1e-6, atol=1e-6)
        self.assertEqual(len(p["xa"]), sum(len(y) for y in ally))

    # 윈도우 수와 날짜 수 — 같은 날짜의 종목은 함께 움직여 독립 관측은 날짜 수에 가까움
    def testcounts(self):
        p = predict_module.panel({"000001": series(220, seed=1), "000002": series(220, seed=2)})
        for split in ("train", "val", "full"):
            with self.subTest(split=split):
                c, tag = p["counts"][split], p["tags"][split]
                self.assertEqual(c["windows"], len(tag))
                self.assertEqual(c["dates"], tag["base"].nunique())
                self.assertEqual(c["windows"], 2 * c["dates"])
        self.assertEqual(len(p["xt"]), p["counts"]["train"]["windows"])
        self.assertEqual(len(p["xv"]), p["counts"]["val"]["windows"])

    # 종목 순서와 무관 (코드 순으로 쌓음)
    def testorder(self):
        frames = trio()
        a = predict_module.panel(frames)
        b = predict_module.panel(dict(reversed(list(frames.items()))))
        self.assertEqual(a["codes"], ["000001", "000002", "000003"])
        self.assertEqual(a["codes"], b["codes"])
        for key in ("xt", "yt", "xv", "yv", "xa", "ya"):
            np.testing.assert_array_equal(a[key], b[key])

    # 쓸 수 없는 종목은 사유와 함께 건너뜀 (윈도우 없음·피처 비유한값), 나머지 종목 결과는 그대로
    def testskip(self):
        bad = series(220, seed=4)
        bad.iloc[150, bad.columns.get_loc("Close")] = np.nan
        frames = {**trio(), "000009": series(30, seed=5), "000008": bad}
        p = predict_module.panel(frames)
        clean = predict_module.panel(trio())
        self.assertEqual(p["codes"], clean["codes"])
        self.assertEqual({s["code"] for s in p["skipped"]}, {"000008", "000009"})
        self.assertTrue(all(s["reason"] for s in p["skipped"]))
        np.testing.assert_array_equal(p["xt"], clean["xt"])

    # 학습 윈도우 10개 미만·검증 윈도우 없음·쓸 종목 없음은 ValueError (조용히 작은 모델을 만들지 않음)
    def testfew(self):
        frames = trio()
        first = min(df.index[0] for df in frames.values())
        for cut in (first + pd.Timedelta(days=50), pd.Timestamp("2027-01-04")):
            with self.subTest(cut=str(cut.date())):
                with self.assertRaises(ValueError):
                    predict_module.panel(frames, cut=cut)
        with self.assertRaises(ValueError):
            predict_module.panel({"000009": series(30, seed=5)})


# 작은 공용 모델 폴더 — offline.pool.deposit()으로 씀 (서빙과 같은 형식), 메타는 필요한 최소 항목
def kit(folder: Path, seed: int = 0) -> tuple[torch.nn.Module, dict]:
    model = predict_module.network(seed, len(FEATURES), SPEC["horizon"], SPEC["seq"]).eval()
    stats = {"mu": [0.0] * len(FEATURES), "sd": [1.0] * len(FEATURES), "ysd": [0.02] * SPEC["horizon"]}
    meta = deposit(folder, model, {"version": VERSION, "spec": PROFILE, "stats": stats})
    return model, meta


# meta.json 항목 바꿔 쓰기
def variant(folder: Path, **fields) -> None:
    meta = json.loads((folder / predict_module.META).read_text(encoding="utf-8"))
    meta.update(fields)
    (folder / predict_module.META).write_text(json.dumps(meta), encoding="utf-8")


# weights.pt 바꿔 쓰기 — rehash면 메타의 해시도 맞춤 (해시 검사를 지나 torch.load·load_state_dict까지 가게)
def payload(folder: Path, blob: bytes, rehash: bool = True) -> None:
    (folder / predict_module.WEIGHTS).write_bytes(blob)
    if rehash:
        variant(folder, weights_sha256=hashlib.sha256(blob).hexdigest())


# 3-3 — 공용 모델 읽기: 없음·손상·사양 불일치·해시 불일치면 None (예외 없음), torch.load는 weights_only=True
class ArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "pooled"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # None이고 이유가 로그에 남음 (운영에서 공용 모델을 안 쓰는 이유를 볼 수 있게)
    def nothing(self) -> None:
        with self.assertLogs(predict_module.logger, level="INFO") as logs:
            self.assertIsNone(predict_module.artifact(self.folder))
        self.assertTrue(logs.output)

    # 정상 — 같은 입력이면 저장 전 모델과 같은 출력, 통계는 numpy 배열, 메타 그대로
    def testroundtrip(self):
        model, meta = kit(self.folder)
        got = predict_module.artifact(self.folder)
        self.assertIsNotNone(got)
        self.assertEqual(got["meta"], meta)
        self.assertFalse(got["meta"]["validated"])
        np.testing.assert_array_equal(got["stats"]["ysd"], np.full(SPEC["horizon"], 0.02))
        x = torch.tensor(np.random.default_rng(1).normal(size=(3, SPEC["seq"], len(FEATURES))), dtype=torch.float32)
        with torch.no_grad():
            self.assertTrue(torch.equal(got["model"](x), model(x)))
        self.assertFalse(got["model"].training)

    # 파일 없음 — 폴더 없음·메타만·가중치만
    def testmissing(self):
        self.nothing()
        kit(self.folder)
        (self.folder / predict_module.WEIGHTS).unlink()
        self.nothing()
        kit(self.folder)
        (self.folder / predict_module.META).unlink()
        self.nothing()

    # 손상 — 메타가 JSON 아님, 가중치가 깨진 바이트(해시는 맞춤), 다른 크기 모델의 가중치(해시는 맞춤)
    def testcorrupt(self):
        kit(self.folder)
        (self.folder / predict_module.META).write_text("{not json", encoding="utf-8")
        self.nothing()
        kit(self.folder)
        payload(self.folder, b"\x00garbage\xff" * 10)
        self.nothing()
        kit(self.folder)
        buf = io.BytesIO()
        torch.save(predict_module.StockTransformer(len(FEATURES), SPEC["horizon"], SPEC["seq"], d_model=16).state_dict(), buf)
        payload(self.folder, buf.getvalue())
        self.nothing()

    # 사양 불일치 — 버전 문자열이 다르거나, 버전은 같아도 사양 dict가 다르면 가중치를 쓰지 않음
    def testspec(self):
        kit(self.folder)
        variant(self.folder, version="v1-000000000000")
        self.nothing()
        kit(self.folder)
        variant(self.folder, spec={**PROFILE, "seq": 30})
        self.nothing()

    # 해시 불일치 — 메타와 다른 가중치(교체 중 끊김·수동 복사 실수)
    def testhash(self):
        kit(self.folder, seed=0)
        other = Path(self.tmp.name) / "other"
        kit(other, seed=1)
        payload(self.folder, (other / predict_module.WEIGHTS).read_bytes(), rehash=False)
        self.nothing()

    # 통계 손상 — 길이 다름·NaN·표준편차 0 이하
    def teststats(self):
        good = {"mu": [0.0] * len(FEATURES), "sd": [1.0] * len(FEATURES), "ysd": [0.02] * SPEC["horizon"]}
        for label, stats in (("short", {**good, "mu": [0.0] * 3}), ("nan", {**good, "sd": [float("nan")] * len(FEATURES)}),
                             ("zero", {**good, "ysd": [0.0] * SPEC["horizon"]}), ("absent", {"mu": good["mu"]})):
            with self.subTest(stats=label):
                kit(self.folder)
                variant(self.folder, stats=stats)
                self.nothing()

    # weights_only=True — 임의 객체를 담은 피클은 실행되지 않고 거부됨 (해시가 맞아도)
    def testpickle(self):
        kit(self.folder)
        buf = io.BytesIO()
        torch.save({"embedding.weight": Tripwire()}, buf)
        payload(self.folder, buf.getvalue())
        _HITS.clear()
        with mock.patch.object(predict_module.torch, "load", wraps=torch.load) as spy:
            self.nothing()
        self.assertEqual(_HITS, [])
        self.assertIs(spy.call_args.kwargs.get("weights_only"), True)


# 3-3 — 서빙 쪽 공용 모델: 처음 필요할 때 settings.pooled_dir에서 한 번 읽고 결과(없음 포함)를 기억
class BundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._dir = settings.pooled_dir

    def tearDown(self) -> None:
        settings.pooled_dir = self._dir
        self.tmp.cleanup()

    # 생성할 때는 읽지 않고, 처음 부를 때 한 번만 읽음
    def testlazy(self):
        sentinel = {"model": None, "stats": {}, "meta": {}}
        calls: list[str] = []
        settings.pooled_dir = str(Path(self.tmp.name) / "pooled")
        with mock.patch.object(predict_module, "artifact", lambda folder: calls.append(str(folder)) or sentinel):
            p = predrig()
            self.assertEqual(calls, [])
            self.assertIs(p.bundle(), sentinel)
            self.assertIs(p.bundle(), sentinel)
        self.assertEqual(calls, [settings.pooled_dir])

    # 없으면 None을 기억 — 나중에 파일이 생겨도 재시작 전까지는 읽지 않음 (운영 반영 순서: 올리기 → 재시작)
    def testabsent(self):
        folder = Path(self.tmp.name) / "pooled"
        settings.pooled_dir = str(folder)
        p = predrig()
        self.assertIsNone(p.bundle())
        kit(folder)
        self.assertIsNone(p.bundle())
        self.assertIsNotNone(predrig().bundle())

    # 기본 위치 — research 볼륨 안 models/pooled (research.py의 {code}.jsonl·evals/와 다른 하위 폴더), 테스트는 없는 경로로 격리
    def testfolder(self):
        default = Path(Settings.model_fields["pooled_dir"].default)
        root = Path(research.__file__).resolve().parents[2] / "data" / "research"
        self.assertEqual(default, root / "models" / "pooled")
        self.assertNotEqual(Path(self._dir), default)
        self.assertFalse(Path(self._dir).exists())


# 부르면 안 되는 자리 (추론 경로가 학습을 시작하면 실패)
def tripwire(*args, **kwargs):
    raise AssertionError("학습 시작됨")


# 3-4 — 공용 모델 추론: 학습 없이 마지막 seq행 피처 → 메타 통계로 표준화 → 예측, 형식은 core()와 같고 source·validated·spec을 더함
class ProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        model = predict_module.network(0, len(FEATURES), SPEC["horizon"], SPEC["seq"]).eval()
        stats = {"mu": np.zeros(len(FEATURES)), "sd": np.ones(len(FEATURES)), "ysd": np.full(SPEC["horizon"], 0.02)}
        self.meta = {"version": VERSION, "validated": False,
                     "metrics": {"mae": 1.5, "mae_zero": 1.6, "skill": 0.0625, "n_val": 40}}
        self.bundle = {"model": model, "stats": stats, "meta": self.meta}
        self.df = series(220)

    # 형식 — 기준가·기준일은 마지막 확정 일봉, 예측은 다음 5개 개장일, 지표는 메타 값
    def testformat(self):
        out = predict_module.projection(self.df, self.bundle, "005930")
        self.assertEqual(set(out), {"predictions", "metrics", "base_close", "base_date", "source", "validated", "spec"})
        self.assertEqual(out["source"], "pooled")
        self.assertIs(out["validated"], False)
        self.assertEqual(out["spec"], VERSION)
        self.assertEqual(out["base_close"], int(round(float(self.df["Close"].iloc[-1]))))
        self.assertIs(type(out["base_close"]), int)
        self.assertEqual(out["base_date"], "2026-11-20")
        self.assertEqual(out["metrics"], self.meta["metrics"])
        self.assertEqual([p["date"] for p in out["predictions"]],
                         ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"])
        for p in out["predictions"]:
            self.assertEqual(set(p), {"date", "close", "change_pct"})

    # 입력은 마지막 seq행 피처뿐 (피처가 20행 앞까지 보므로 끝에서 41번째 행부터는 무관), 학습 함수를 부르지 않음, 같은 입력 → 같은 출력
    def testwindow(self):
        old = self.df.copy()
        old.iloc[: len(old) - 40, old.columns.get_loc("Close")] *= 1.3
        near = self.df.copy()
        near.iloc[-1, near.columns.get_loc("Volume")] *= 50
        with mock.patch.object(predict_module, "trial", tripwire), \
             mock.patch.object(predict_module, "fullmodel", tripwire), \
             mock.patch.object(predict_module, "network", tripwire):
            first = predict_module.projection(self.df, self.bundle)
            again = predict_module.projection(self.df.copy(), self.bundle)
            far = predict_module.projection(old, self.bundle)
            moved = predict_module.projection(near, self.bundle)
        self.assertEqual(first, again)
        self.assertEqual(first, far)
        self.assertNotEqual(first["predictions"], moved["predictions"])

    # validated는 메타 값이 정확히 true일 때만 True (문자열·1·없음은 False), spec은 메타의 버전(없으면 None)
    def testflag(self):
        for value, want in ((True, True), (False, False), ("true", False), (1, False), (None, False)):
            with self.subTest(value=value):
                out = predict_module.projection(self.df, {**self.bundle, "meta": {**self.meta, "validated": value}})
                self.assertIs(out["validated"], want)
        out = predict_module.projection(self.df, {**self.bundle, "meta": {}})
        self.assertIs(out["validated"], False)
        self.assertIsNone(out["spec"])
        self.assertEqual(out["metrics"], {})

    # 확정 일봉 200행 미만이면 예측하지 않음 (종목별 학습과 같은 기준), latest()는 마지막 seq행 (모자라면 ValueError)
    def testshort(self):
        with self.assertRaises(ValueError):
            predict_module.projection(series(199), self.bundle)
        feats = predict_module.features(self.df)
        np.testing.assert_array_equal(predict_module.latest(feats, SPEC["seq"]),
                                      feats.to_numpy(dtype=float)[-SPEC["seq"]:])
        with self.assertRaises(ValueError):
            predict_module.latest(feats.iloc[: SPEC["seq"] - 1], SPEC["seq"])


# 3-5 — 서빙: 공용 모델이 있으면 일봉 조회 + 추론만 (학습 없음), 없거나 못 읽으면 종목별 즉석 학습 (source per_symbol·validated false)
class ServingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._dir = settings.pooled_dir
        self.folder = Path(self.tmp.name) / "pooled"
        settings.pooled_dir = str(self.folder)
        self.df = series(220)
        self.calls: list[str] = []

    def tearDown(self) -> None:
        settings.pooled_dir = self._dir
        self.tmp.cleanup()

    # 일봉 조회 자리 — 부른 종목을 기록하고 합성 일봉을 돌려줌
    def rig(self):
        p = predrig()
        p.raw = lambda symbol: self.calls.append(symbol) or self.df.copy()
        return p

    # 공용 모델 있음 — raw() 한 번 + projection(), 학습 함수를 부르지 않음, 결과는 저장 모델 추론과 같고 forecast()가 v4 키로 캐시
    def testpooled(self):
        kit(self.folder)
        p = self.rig()
        with mock.patch.object(predict_module, "trial", tripwire), \
             mock.patch.object(predict_module, "fullmodel", tripwire), \
             mock.patch.object(predict_module, "network", tripwire):
            out = asyncio.run(p.forecast("5930"))
        p._executor.shutdown(wait=True)
        self.assertEqual(self.calls, ["005930"])
        want = predict_module.projection(self.df, predict_module.artifact(self.folder), "005930")
        self.assertEqual(out, want)
        self.assertEqual((out["source"], out["validated"], out["spec"]), ("pooled", False, VERSION))
        self.assertEqual(p.cached("005930"), out)
        self.assertEqual(predict_module._KEY, "pred:v4:")

    # 공용 모델 없음·사양 불일치·손상 — 예외 없이 종목별 즉석 학습으로 (화면 표시용), core()와 같은 결과에 source·validated·spec
    def testfallback(self):
        cases = (("absent", lambda: None),
                 ("spec", lambda: (kit(self.folder), variant(self.folder, version="v0-000000000000"))),
                 ("corrupt", lambda: (kit(self.folder), payload(self.folder, b"garbage"))))
        for label, prep in cases:
            with self.subTest(case=label):
                prep()
                p = self.rig()
                out = p.training("005930", epochs=1)
                p._executor.shutdown(wait=True)
                self.assertIsNone(p.bundle())
                self.assertEqual((out["source"], out["validated"], out["spec"]), ("per_symbol", False, VERSION))
                alt = p.core(self.df.copy(), seed=predict_module.codeseed("005930"), epochs=1)
                self.assertEqual(out, alt)


if __name__ == "__main__":
    unittest.main()
