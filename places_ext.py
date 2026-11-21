"""
센서 없는 카페(정릉 일대 카카오 CE7 목록)를 /places 에 붙이는 확장.

- 카페 목록은 DB 테이블 `places` 에서 읽음 (1분 캐시). 테이블이 없거나 비어 있으면 아무 일도 안 함.
- 센서 카페(app_api.PLACES)는 지금과 똑같이 app_api 가 응답함. 이 파일은 그 외 id 만 처리.
- 센서 없는 카페의 /indoor 는 오류 없이 24시간 모두 gray(source "none") 로 내려감.
  나중에 predictions 테이블에 그 카페 행이 생기면 source "estimated", confidence "low" 로 내려감.

설치: app_api.py 맨 끝에 한 줄 `import places_ext` 추가 (main.py 는 그대로).
되돌리기: 그 한 줄만 지우고 재시작.
"""

import math

from fastapi import APIRouter, Query

import app_api as A

PLACES_TTL = 60          # places 테이블 캐시 (초)

_ext = APIRouter()


# ---------------------------------------------------------------------------
# 목록
# ---------------------------------------------------------------------------
def _extra_places():
    """{id: {"name", "lat", "lng"}}  센서 카페 id 와 겹치는 건 무시."""
    def load():
        try:
            rows = A._q("SELECT id, name, lat, lng FROM places ORDER BY name")
        except Exception:
            return {}   # 테이블이 아직 없음
        return {r["id"]: {"name": r["name"], "lat": float(r["lat"]), "lng": float(r["lng"])}
                for r in rows if r["id"] not in A.PLACES}
    return A._cache.get("extra_places", PLACES_TTL, load)


def _dist_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _nearest_sensor(p):
    best = None
    for pid, sp in A.PLACES.items():
        d = _dist_m(p["lat"], p["lng"], sp["lat"], sp["lng"])
        if best is None or d < best[1]:
            best = (pid, d)
    if not best:
        return None
    return {"id": best[0], "name": A.PLACES[best[0]]["name"], "distance_m": int(round(best[1]))}


@_ext.get("/places")
def list_places():
    base = A.list_places()
    for item in base:
        item["has_sensor"] = item["id"] in A.PLACES
    extra = [{"id": k, "name": v["name"], "lat": v["lat"], "lng": v["lng"],
              "is_demo": False, "has_sensor": False}
             for k, v in _extra_places().items()]
    return base + extra


# ---------------------------------------------------------------------------
# 센서 없는 카페 응답
# ---------------------------------------------------------------------------
def _gray_factor():
    return {"value": None, "level": "gray", "source": "none", "confidence": None}


def _estimated_hours(place_id, d, p):
    near = _nearest_sensor(p)
    # 소음·조도 기준은 가장 가까운 센서 카페 것을 빌려 씀 (온도·습도는 고정 기준)
    th = A._thresholds(near["id"]) if near else {}
    preds = A._predictions(place_id, d)
    hours = []
    for h in range(24):
        pr = preds.get(h)
        factors = {}
        for f in A.FACTORS:
            if pr and pr.get(f) is not None:
                v = float(pr[f])
                factors[f] = {"value": A._num(v), "level": A._level(f, v, th),
                              "source": "estimated", "confidence": pr.get("confidence") or "low"}
            else:
                factors[f] = _gray_factor()
        level, worst = A._overall(factors)
        est = any(x["source"] == "estimated" for x in factors.values())
        hours.append({
            "hour": h,
            "level": level,
            "worst_factors": worst,
            "source": "estimated" if est else "none",
            "confidence": ("high" if all(x["confidence"] == "high" for x in factors.values()
                                         if x["source"] == "estimated") else "low") if est else None,
            "factors": factors,
        })
    return hours, near


@_ext.get("/places/{place_id}/indoor")
def indoor(place_id: str, date: str = Query(None, description="YYYY-MM-DD (KST), 생략 시 오늘")):
    p = _extra_places().get(place_id)
    if not p:
        return A.indoor(place_id, date)
    d = A._parse_date(date)
    hours, near = _estimated_hours(place_id, d, p)
    return {"place_id": place_id, "is_demo": False, "has_sensor": False,
            "date": d.isoformat(), "day_type": A._day_type(d),
            "nearest_sensor_place": near, "hours": hours}


@_ext.get("/places/{place_id}/street")
def street(place_id: str, date: str = Query(None, description="YYYY-MM-DD (KST), 생략 시 오늘")):
    p = _extra_places().get(place_id)
    if not p:
        return A.street(place_id, date)
    d = A._parse_date(date)
    weather = A._weather_for_day(d)
    hours = [{"hour": h, "population": None, "population_source": "none",
              "crowd": None, "crowd_level": None, "weather": weather[h]} for h in range(24)]
    return {"place_id": place_id, "is_demo": False, "has_sensor": False,
            "date": d.isoformat(), "day_type": A._day_type(d), "hours": hours}


def _summary_extra(name, level, worst, factors, near):
    topic = A._topic(name)
    if level == "gray":
        s = f"{topic} 아직 센서가 없어서 상태를 알 수 없어요."
        if near:
            s += f" 가장 가까운 {near['name']}(약 {near['distance_m']}m) 정보를 참고해 주세요."
        return s
    if level == "green":
        return f"{topic} 추정값으로는 지금 쾌적해요. 실제와 다를 수 있어요."
    names = ", ".join(A.FACTOR_KO[f] for f in worst)
    return f"{topic} 추정값으로는 지금 {names} 때문에 {A.LEVEL_KO[level]}이에요. 실제와 다를 수 있어요."


@_ext.get("/places/{place_id}/now")
def now(place_id: str):
    p = _extra_places().get(place_id)
    if not p:
        return A.now(place_id)
    nk = A._now_kst()
    hours, near = _estimated_hours(place_id, nk.date(), p)
    cur = hours[nk.hour]
    factors = {f: {"value": v["value"], "level": v["level"]} for f, v in cur["factors"].items()}
    w = A._q("""SELECT obs_time, temp, humidity, rain_15m, rain_60m
                FROM weather_now WHERE stn=108 ORDER BY obs_time DESC LIMIT 1""")
    weather = None
    if w:
        w = w[0]
        weather = {"obs_time": str(w["obs_time"]), "temp": A._num(w["temp"]),
                   "humidity": A._num(w["humidity"]), "rain_15m": A._num(w["rain_15m"]),
                   "rain_60m": A._num(w["rain_60m"])}
    return {
        "place_id": place_id,
        "is_demo": False,
        "has_sensor": False,
        "time": nk.strftime("%Y-%m-%dT%H:%M:%S"),
        "indoor": {"level": cur["level"], "worst_factors": cur["worst_factors"], "factors": factors,
                   "source": cur["source"], "confidence": cur["confidence"],
                   "measured_at": None, "stale": True},
        "street": {"population": None, "population_source": "none", "crowd_level": None,
                   "weather": weather},
        "nearest_sensor_place": near,
        "summary_ko": _summary_extra(p["name"], cur["level"], cur["worst_factors"], factors, near),
    }


# app_api 의 같은 경로보다 먼저 매칭되도록 앞에 끼워 넣음
A.router.routes[:0] = _ext.routes
