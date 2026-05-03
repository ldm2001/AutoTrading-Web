# 시장 국면 필터 — 지수 급락 시 신규 매수 차단

def regime(
    indices: list[dict],
    *,
    risk_off_avg_pct: float = -1.0,
    risk_off_single_pct: float = -2.0,
    risk_on_avg_pct: float = 0.5,
) -> dict:
    changes = [
        float(item.get("change_percent", 0))
        for item in indices
        if item.get("change_percent") is not None
    ]
    if not changes:
        return {
            "state": "unknown",
            "allow_new_buys": True,
            "avg_change_pct": 0.0,
            "reason": "시장 지수 데이터 없음",
        }

    avg = round(sum(changes) / len(changes), 2)
    worst = min(changes)

    if avg <= risk_off_avg_pct or worst <= risk_off_single_pct:
        return {
            "state": "risk_off",
            "allow_new_buys": False,
            "avg_change_pct": avg,
            "worst_change_pct": round(worst, 2),
            "reason": "지수 동반 약세로 신규 매수 차단",
        }

    if avg >= risk_on_avg_pct:
        return {
            "state": "risk_on",
            "allow_new_buys": True,
            "avg_change_pct": avg,
            "worst_change_pct": round(worst, 2),
            "reason": "지수 흐름 양호",
        }

    return {
        "state": "neutral",
        "allow_new_buys": True,
        "avg_change_pct": avg,
        "worst_change_pct": round(worst, 2),
        "reason": "지수 흐름 중립",
    }
