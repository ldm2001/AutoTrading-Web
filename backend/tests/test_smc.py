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
