# SMC 지표 모듈 — 국장 오버나잇 갭 필터 내장
import datetime
import numpy as np

# 한국 장 시간 상수
_OPEN  = datetime.time(9, 0)
_CLOSE = datetime.time(15, 30)

# 평일 장중(09:00~15:30) 여부
def is_intraday(dt: datetime.datetime) -> bool:
    return dt.weekday() < 5 and _OPEN <= dt.time() <= _CLOSE

# 동일 거래일 여부 — 오버나잇 갭 FVG 오인 방지
def same_session(a: datetime.datetime, b: datetime.datetime) -> bool:
    return a.date() == b.date()

# 장외 캔들 제거 (09:00 이전/15:30 이후)
def intraday_only(candles: list[dict]) -> list[dict]:
    return [c for c in candles if is_intraday(c["time"])]

# COI 근사값, 기관 방향성 프록시
def _body_ratio(c: dict) -> float:
    rng = c["high"] - c["low"]
    if rng == 0:
        return 0.0
    return (c["close"] - c["open"]) / rng

# FVG 탐지 — Bullish: prev.high < nxt.low, Bearish: prev.low > nxt.high, 동일 세션만 유효
def fvg_zones(candles: list[dict], join_consecutive: bool = True) -> list[dict]:
    n = len(candles)
    result: list[dict] = []

    for i in range(1, n - 1):
        prev, mid, nxt = candles[i - 1], candles[i], candles[i + 1]

        # 오버나잇 갭을 FVG로 오인하지 않음
        if "time" in mid:
            if not (same_session(prev["time"], mid["time"]) and
                    same_session(mid["time"], nxt["time"])):
                continue

        is_bull = mid["close"] > mid["open"]
        is_bear = mid["close"] < mid["open"]

        # 이전 고가 < 다음 저가 
        if is_bull and prev["high"] < nxt["low"]:
            result.append({
                "kind":      "bullish",
                "top":       float(nxt["low"]),
                "bottom":    float(prev["high"]),
                "index":     i,
                "label":     str(mid.get("date", mid.get("time", i))),
                "mitigated": False,
            })

        # 이전 저가 > 다음 고가 
        elif is_bear and prev["low"] > nxt["high"]:
            result.append({
                "kind":      "bearish",
                "top":       float(prev["low"]),
                "bottom":    float(nxt["high"]),
                "index":     i,
                "label":     str(mid.get("date", mid.get("time", i))),
                "mitigated": False,
            })

    # top은 최대 bottom은 최소로 확장
    if join_consecutive and len(result) >= 2:
        merged: list[dict] = []
        cur = dict(result[0])
        for j in range(1, len(result)):
            nxt_fvg = result[j]
            # 연속이고 같은 방향이면 병합
            if (nxt_fvg["kind"] == cur["kind"] and
                    nxt_fvg["index"] == cur["index"] + 1):
                cur["top"]    = max(cur["top"],    nxt_fvg["top"])
                cur["bottom"] = min(cur["bottom"], nxt_fvg["bottom"])
            else:
                merged.append(cur)
                cur = dict(nxt_fvg)
        merged.append(cur)
        result = merged

    return result

# 스윙 고저 탐지 — 전후 swing_length 캔들 대비 극값, 연속 중복 제거
def swing_hl(candles: list[dict], swing_length: int = 5) -> list[dict]:
    n     = len(candles)
    highs = np.array([c["high"] for c in candles], dtype=float)
    lows  = np.array([c["low"]  for c in candles], dtype=float)

    raw: list[dict] = []
    for i in range(swing_length, n - swing_length):
        win_h = highs[i - swing_length: i + swing_length + 1]
        win_l = lows [i - swing_length: i + swing_length + 1]
        if highs[i] == win_h.max():
            raw.append({"index": i, "kind": "high", "level": float(highs[i])})
        elif lows[i] == win_l.min():
            raw.append({"index": i, "kind": "low",  "level": float(lows[i])})

    # 연속 같은 방향 -> 더 극단적인 쪽만 유지
    cleaned: list[dict] = []
    for item in raw:
        if cleaned and cleaned[-1]["kind"] == item["kind"]:
            prev = cleaned[-1]
            if item["kind"] == "high" and item["level"] >= prev["level"]:
                cleaned[-1] = item
            elif item["kind"] == "low" and item["level"] <= prev["level"]:
                cleaned[-1] = item
        else:
            cleaned.append(item)

    return cleaned

# OB 탐지 — 스윙 돌파 직전 최저저가(Bullish)/최고고가(Bearish) 캔들, strength=body_ratio
def ob_zones(candles: list[dict], swing_length: int = 5) -> list[dict]:
    swings  = swing_hl(candles, swing_length)
    n       = len(candles)
    result: list[dict] = []
    crossed: set[int]  = set()

    # Bullish OB: 스윙 고가 돌파
    for sh in (s for s in swings if s["kind"] == "high"):
        hi     = sh["index"]
        sh_lvl = sh["level"]
        if hi in crossed:
            continue

        for j in range(hi + 1, n):
            if candles[j]["close"] > sh_lvl:
                crossed.add(hi)
                # hi+1 ~ j-1 구간에서 최저 저가 캔들 선택
                seg = candles[hi + 1: j] or [candles[j - 1]]
                ob_c = min(seg, key=lambda c: c["low"])
                ob_i = hi + 1 + seg.index(ob_c)

                # 분봉: 동일 세션 체크
                if "time" in ob_c:
                    if not same_session(ob_c["time"], candles[j]["time"]):
                        break

                result.append({
                    "kind":      "bullish",
                    "top":       float(ob_c["high"]),
                    "bottom":    float(ob_c["low"]),
                    "index":     ob_i,
                    "label":     str(ob_c.get("date", ob_c.get("time", ob_i))),
                    "strength":  abs(_body_ratio(ob_c)),
                    "mitigated": False,
                })
                break

    # Bearish OB: 스윙 저가 하향 돌파
    for sl in (s for s in swings if s["kind"] == "low"):
        li     = sl["index"]
        sl_lvl = sl["level"]
        if li in crossed:
            continue

        for j in range(li + 1, n):
            if candles[j]["close"] < sl_lvl:
                crossed.add(li)
                # li+1 ~ j-1 구간에서 최고 고가 캔들 선택
                seg = candles[li + 1: j] or [candles[j - 1]]
                ob_c = max(seg, key=lambda c: c["high"])
                ob_i = li + 1 + seg.index(ob_c)

                if "time" in ob_c:
                    if not same_session(ob_c["time"], candles[j]["time"]):
                        break

                result.append({
                    "kind":      "bearish",
                    "top":       float(ob_c["high"]),
                    "bottom":    float(ob_c["low"]),
                    "index":     ob_i,
                    "label":     str(ob_c.get("date", ob_c.get("time", ob_i))),
                    "strength":  abs(_body_ratio(ob_c)),
                    "mitigated": False,
                })
                break

    return result

# BOS/CHoCH 탐지 — 마지막 4개 스윙 패턴으로 추세 지속(BOS)/전환(CHoCH) 판단
def structure_break(candles: list[dict], swing_length: int = 5) -> dict:
    swings = swing_hl(candles, swing_length)
    if len(swings) < 4:
        return {"bos": 0, "choch": 0, "level": 0.0}

    last4  = swings[-4:]
    kinds  = [s["kind"]  for s in last4]
    levels = [s["level"] for s in last4]

    # Bullish BOS: low-high-low-high + 모두 상승 (HH-HL 구조)
    if kinds == ["low", "high", "low", "high"]:
        if levels[0] < levels[2] and levels[1] < levels[3]:
            return {"bos": 1, "choch": 0, "level": levels[2]}

    # Bearish BOS: high-low-high-low + 모두 하락 (LH-LL 구조)
    if kinds == ["high", "low", "high", "low"]:
        if levels[0] > levels[2] and levels[1] > levels[3]:
            return {"bos": -1, "choch": 0, "level": levels[2]}

    # Bullish CHoCH: 하락 구조(LH-LL)에서 최근 고점 돌파
    if kinds == ["high", "low", "high", "low"]:
        if levels[2] < levels[0] and levels[3] < levels[1]:
            if levels[3] > levels[1]:   # 저점이 갱신되지 않음 → CHoCH
                return {"bos": 0, "choch": 1, "level": levels[2]}

    # Bearish CHoCH: 상승 구조(HH-HL)에서 최근 저점 하향 이탈
    if kinds == ["low", "high", "low", "high"]:
        if levels[2] > levels[0] and levels[3] > levels[1]:
            if levels[3] < levels[1]:
                return {"bos": 0, "choch": -1, "level": levels[2]}

    return {"bos": 0, "choch": 0, "level": 0.0}

# FVG/OB 구간 진입 시 mitigated=True 마킹
def mitigate(zones: list[dict], price: float) -> None:
    for z in zones:
        if z["mitigated"]:
            continue
        if z["kind"] == "bullish" and price <= z["top"]:
            z["mitigated"] = True
        elif z["kind"] == "bearish" and price >= z["bottom"]:
            z["mitigated"] = True

# 미완화 구간만 반환
def active_zones(zones: list[dict]) -> list[dict]:
    return [z for z in zones if not z["mitigated"]]

# 현재가 가장 근접 미완화 구간
def _nearest_zone(zones: list[dict], price: float) -> dict | None:
    active = active_zones(zones)
    if not active:
        return None
    return min(active, key=lambda z: abs(price - (z["top"] + z["bottom"]) / 2))

# FVG 근접도 점수 (-8~+8) — Bullish 내부/근접 양수, Bearish 음수
def fvg_score(candles: list[dict], price: float) -> tuple[float, str]:
    fvgs = fvg_zones(candles)
    z    = _nearest_zone(fvgs, price)
    if z is None:
        return 0.0, "FVG 없음"

    mid    = (z["top"] + z["bottom"]) / 2
    dist   = abs(price - mid) / mid * 100
    inside = z["bottom"] <= price <= z["top"]

    if z["kind"] == "bullish":
        if inside:
            return 8.0, f"Bullish FVG 내부 진입 ({z['bottom']:,.0f}~{z['top']:,.0f})"
        if dist < 0.3:
            return 5.0, f"Bullish FVG 근접 (거리 {dist:.2f}%)"
        if dist < 1.0:
            return 2.0, f"Bullish FVG 접근 (거리 {dist:.2f}%)"
        return 0.0, f"FVG 원격 ({dist:.1f}%)"
    else:
        if inside:
            return -8.0, f"Bearish FVG 내부 ({z['bottom']:,.0f}~{z['top']:,.0f})"
        if dist < 0.3:
            return -5.0, f"Bearish FVG 근접 (거리 {dist:.2f}%)"
        if dist < 1.0:
            return -2.0, f"Bearish FVG 접근 (거리 {dist:.2f}%)"
        return 0.0, f"FVG 원격 ({dist:.1f}%)"


# OB 지지/저항 점수 (-7~+7) — strength(body_ratio) 가중, Bullish 양수, Bearish 음수
def ob_score(candles: list[dict], price: float) -> tuple[float, str]:
    obs = ob_zones(candles)
    z   = _nearest_zone(obs, price)
    if z is None:
        return 0.0, "Order Block 없음"

    mid      = (z["top"] + z["bottom"]) / 2
    dist     = abs(price - mid) / mid * 100
    inside   = z["bottom"] <= price <= z["top"]
    strength = z.get("strength", 0.5) # body_ratio proxy
    max_pts  = 7.0 * max(strength, 0.3) # strength 가중 최대 점수

    if z["kind"] == "bullish":
        if inside:
            return round(max_pts, 1), f"Bullish OB 내부 ({z['bottom']:,.0f}~{z['top']:,.0f}, 강도 {strength:.2f})"
        if price > z["top"] and dist < 0.3:
            return round(max_pts * 0.65, 1), f"Bullish OB 직상단 (거리 {dist:.2f}%, 강도 {strength:.2f})"
        if dist < 1.0:
            return round(max_pts * 0.3, 1), f"Bullish OB 근접 (거리 {dist:.2f}%)"
        return 0.0, f"OB 원격 ({dist:.1f}%)"
    else:
        if inside:
            return round(-max_pts, 1), f"Bearish OB 내부 ({z['bottom']:,.0f}~{z['top']:,.0f}, 강도 {strength:.2f})"
        if price < z["bottom"] and dist < 0.3:
            return round(-max_pts * 0.65, 1), f"Bearish OB 직하단 (거리 {dist:.2f}%, 강도 {strength:.2f})"
        if dist < 1.0:
            return round(-max_pts * 0.3, 1), f"Bearish OB 근접 (거리 {dist:.2f}%)"
        return 0.0, f"OB 원격 ({dist:.1f}%)"


# BOS/CHoCH 구조 점수 (-5~+5) — BOS 추세 지속, CHoCH 전환 경고
def structure_score(candles: list[dict]) -> tuple[float, str]:
    sb = structure_break(candles)

    if sb["bos"] == 1:
        return 5.0, f"Bullish BOS 확인 (레벨 {sb['level']:,.0f})"
    if sb["bos"] == -1:
        return -5.0, f"Bearish BOS 확인 (레벨 {sb['level']:,.0f})"
    if sb["choch"] == 1:
        return 3.0, f"Bullish CHoCH — 하락→상승 전환 신호 (레벨 {sb['level']:,.0f})"
    if sb["choch"] == -1:
        return -3.0, f"Bearish CHoCH — 상승→하락 전환 신호 (레벨 {sb['level']:,.0f})"
    return 0.0, "구조 중립"

# SMC 전체 분석 요약 — fvg/ob/str 점수 + active 구간 수
def analysis(candles: list[dict], price: float) -> dict:
    fvg_s, fvg_r = fvg_score(candles, price)
    ob_s,  ob_r  = ob_score(candles, price)
    str_s, str_r = structure_score(candles)

    fvgs = fvg_zones(candles)
    obs  = ob_zones(candles)

    return {
        "fvg_score":   round(fvg_s, 1),
        "fvg_reason":  fvg_r,
        "ob_score":    round(ob_s, 1),
        "ob_reason":   ob_r,
        "str_score":   round(str_s, 1),
        "str_reason":  str_r,
        "total":       round(fvg_s + ob_s + str_s, 1),
        "active_fvgs": len(active_zones(fvgs)),
        "active_obs":  len(active_zones(obs)),
    }
