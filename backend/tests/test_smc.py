import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.market import smc


# 테스트용 캔들 (high/low/open/close)
def candle(high, low, open_, close):
    return {"high": float(high), "low": float(low), "open": float(open_), "close": float(close)}


# bottom=100, top=120, index=1 인 bullish FVG 생성용 3캔들
def _fvg_base():
    return [
        candle(100, 95, 96, 99),
        candle(130, 101, 101, 128),
        candle(124, 120, 121, 123),
    ]


# 분봉 테스트용 캔들 — 같은 날 HHMM 시각 포함
def tbar(hhmm: str, high, low, open_, close):
    at = datetime.datetime(2026, 10, 21, int(hhmm[:2]), int(hhmm[2:]))
    return {"time": at, **candle(high, low, open_, close)}


# 인덱스 1~3 3봉에서만 bullish FVG(100~120)가 생기는 4봉 — times로 구멍 위치 지정 (기본 09:15→11:30)
def _holed(times=("0900", "0915", "1130", "1145")):
    prices = [(102, 94, 95, 97), (100, 95, 96, 99), (130, 101, 101, 128), (124, 120, 121, 123)]
    return [tbar(t, *p) for t, p in zip(times, prices)]


# 분봉 시계열 구멍 — 같은 날이라도 봉 간격이 끊긴 3봉은 FVG가 아님 (c15 3구간 수집·틱 누락 구간)
class SmcContinuityTest(unittest.TestCase):
    # 구멍이 가운데 봉 왼쪽·오른쪽·양쪽 어디든 건너는 3봉은 FVG 아님 — 인덱스 인접만 보면 100~120 bullish FVG 오탐
    # 양쪽 구멍(135분·135분)은 간격이 같아도 최소 간격(15분)이 아니라 제외
    def test_fvg_skips_time_hole(self):
        for times in (("0900", "0915", "1130", "1145"),
                      ("0900", "0915", "0930", "1145"),
                      ("0900", "0915", "1130", "1345")):
            with self.subTest(times=times):
                self.assertEqual(smc.fvgz(_holed(times)), [])

    # 구조적 손절가도 구멍 FVG로 잡지 않음 — 오탐이면 하단 100이 손절가가 됨
    def test_stop_skips_time_hole(self):
        self.assertIsNone(smc.stop(_holed(), 130.0))

    # 같은 가격이라도 15분 연속이면 FVG 유지 — 시각 있는 봉 FVG를 통째로 버리는 과잉 필터면 실패
    def test_fvg_keeps_continuous_15m(self):
        zones = smc.fvgz(_holed(("0900", "0915", "0930", "0945")))
        self.assertEqual([(z["kind"], z["bottom"], z["top"]) for z in zones], [("bullish", 100.0, 120.0)])

    # 봉 간격은 시계열에서 추정 — 60분 연속도 FVG 유지 (15분 고정이면 실패)
    def test_fvg_keeps_continuous_60m(self):
        self.assertEqual(len(smc.fvgz(_holed(("0900", "1000", "1100", "1200")))), 1)


# FVG/OB mitigation — 메워진 구간이 지지선/점수에서 빠지는지 검증
class SmcMitigationTest(unittest.TestCase):
    # sweep: 형성 이후 캔들이 구간에 진입하면 mitigated
    def test_sweep_marks_touched(self):
        zone = {"kind": "bullish", "top": 120.0, "bottom": 100.0, "index": 1, "mitigated": False}
        candles = _fvg_base() + [candle(116, 110, 115, 112)]  # index 3, 저가 110 ≤ top
        smc.sweep(candles, [zone])
        self.assertTrue(zone["mitigated"])

    # sweep: 구간 위에서만 움직이면 live 유지
    def test_sweep_keeps_untouched(self):
        zone = {"kind": "bullish", "top": 120.0, "bottom": 100.0, "index": 1, "mitigated": False}
        candles = _fvg_base() + [candle(128, 125, 126, 127)]  # index 3, 저가 125 > top
        smc.sweep(candles, [zone])
        self.assertFalse(zone["mitigated"])

    # stop(): 메워진 FVG는 구조적 손절가에서 제외
    def test_stop_excludes_mitigated(self):
        touched = _fvg_base() + [candle(116, 110, 115, 112)]
        self.assertIsNone(smc.stop(touched, 130.0))

    # stop(): 살아있는 FVG 하단은 손절가로 사용
    def test_stop_uses_live(self):
        live = _fvg_base() + [candle(128, 125, 126, 127)]
        self.assertEqual(smc.stop(live, 130.0), 100.0)


if __name__ == "__main__":
    unittest.main()
