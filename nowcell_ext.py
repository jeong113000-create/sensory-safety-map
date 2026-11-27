"""
센서 카페 지금 칸·/now 출처 표시 (2026-11-22).

1) /indoor 오늘 날짜의 "지금 시각" 칸을 실측으로: /now 와 같은 최근 10분 평균 값·색, source "measured".
   센서가 끊겨 값이 없으면 그 항목은 gray "none" (시연용·예측값을 지금 칸에 넣지 않음).
   다음 시각부터는 그대로(정릉동커피는 "demo", 포틀리에는 "model").
2) /now 에 level_source("sensor" / 값이 하나도 없으면 "none")와 indoor.source("measured" / "none").
   센서 없는 카페도 모두 level_source(estimated/demo/report/none)와 indoor.source 를 빠짐없이 넣음 (11/22 v2).

대상: 센서 카페(cafe-A, cafe-B). 설치: app_api.py 맨 끝(hours2_ext 아래)에 `import nowcell_ext` 한 줄.
"""

import app_api as A
import hours_ext as HX
import sens_ext as SE

ITEMS = ("noise", "temp", "humidity")


def _now_ind(pid):
    def load():
        try:
            return A.now(pid).get("indoor") or {}
        except Exception:
            return {}
    return A._cache.get(f"nowcell:{pid}", 60, load)


def _has_value(ind):
    return any(((ind.get("factors") or {}).get(f) or {}).get("value") is not None for f in ITEMS)


_prev_indoor = SE._orig_indoor


def indoor(place_id, date):
    out = _prev_indoor(place_id, date)
    if place_id not in A.PLACES or not isinstance(out, dict):
        return out
    nk = A._now_kst()
    if A._parse_date(date) != nk.date():
        return out
    ind = _now_ind(place_id)
    for i, e in enumerate(out.get("hours") or []):
        if not isinstance(e, dict) or e.get("hour") != nk.hour:
            continue
        factors = {}
        for f, v in (ind.get("factors") or {}).items():
            val = (v or {}).get("value")
            src = "measured" if val is not None else "none"
            if f == "light":
                src = (v or {}).get("source") or src
            factors[f] = {"value": val, "level": (v or {}).get("level") or "gray",
                          "source": src, "confidence": "high" if val is not None else None}
        has = _has_value(ind)
        cell = {"hour": e["hour"], "level": ind.get("level") or "gray",
                "worst_factors": ind.get("worst_factors") or [],
                "score": ind.get("score"), "grade": ind.get("grade"),
                "source": "measured" if has else "none", "confidence": "high" if has else None,
                "factors": factors or e.get("factors")}
        if "closed" in e:
            cell["closed"] = e["closed"]
            if e["closed"]:
                HX._gray_hour(cell)
        out["hours"][i] = cell
    return out


_prev_now = SE._orig_now


IND_SRC = {"sensor": "measured", "estimated": "estimated", "demo": "demo", "report": "report", "none": "none"}


def now(place_id):
    out = _prev_now(place_id)
    if not isinstance(out, dict):
        return out
    ind = out.setdefault("indoor", {})
    if place_id not in A.PLACES:
        ls = out.get("level_source") or "none"
        out["level_source"] = ls
        ind["source"] = ind.get("source") or IND_SRC.get(ls, "none")
        return out
    has = _has_value(ind)
    ind["source"] = "measured" if has else "none"
    out["level_source"] = "sensor" if has else "none"
    return out


SE._orig_indoor, SE._orig_now = indoor, now
