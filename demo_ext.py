"""
지도 시연용 확장 (2026-11-14).

1) 시연(예측) 카페 2곳(DEMO): is_demo=true, /indoor 24시간·/street·/now 를 시연값으로 응답.
   값은 DB 에 넣지 않고 이 파일에서 시간대 패턴으로 만듦 (sensor_data/predictions 오염 방지).
   색은 실제 카페와 같은 점수식(sensory_score)으로 계산함.
2) /now: 센서도 시연값도 없는 카페는 500 대신 200 + indoor.level "gray", data_available false.
   없는 place_id 는 404.
3) /places: 모든 장소에 level(현재 시각), is_demo, has_sensor, level_source 를 넣음.
4) (11/15) 종합 level 에서 조도 제외: 소음·온도·습도 항목 색 중 가장 나쁜 색.
   factors.light 는 응답에 그대로 남음(색 계산에만 안 씀). score/grade 는 조도 뺀 가중 점수(참고용).
   /thresholds 도 지금 기준으로 바꿈.

설치: app_api.py 맨 끝 `import score_ext` 아래에 `import demo_ext` 한 줄.
되돌리기: 그 한 줄만 지우고 재시작.
"""

from fastapi import APIRouter, HTTPException, Query

import app_api as A
import places_ext as PX
import score_ext as SX
import sensory_score as SS

# 시연(예측) 카페: place_id -> 성향. 사용자가 고른 2곳 (2026-11-14).
#   "red"    : 오전 한산(초록) → 점심 혼잡(빨강) → 오후 중간(노랑) → 저녁 혼잡(빨강) → 밤 한산(초록)
#   "yellow" : 같은 패턴인데 가장 나쁠 때가 노랑 (점심·오후·저녁 노랑)
DEMO = {
    "kakao-1795099660": "yellow",   # 차담by갤러리더스퀘어
    "kakao-92860343": "red",        # 카페쿠피
}

# 시간대(KST) → 혼잡 단계 0 한산 / 1 중간 / 2 혼잡
CROWD = {h: 0 for h in range(24)}                       # 21시~10시 한산
CROWD.update({11: 2, 12: 2, 13: 2,                     # 점심 혼잡
              14: 1, 15: 1, 16: 1,                     # 오후 중간
              17: 2, 18: 2, 19: 2, 20: 2})             # 저녁 혼잡
PATTERN = {"red": ("green", "yellow", "red"), "yellow": ("green", "yellow", "yellow")}

DEMO_TEMP, DEMO_HUM = 22.5, 50.0     # 온습도는 늘 쾌적 범위 → 색은 소음이 정함
DEMO_LIGHT = 8.5                     # 조도점수(0~10, 제보 만족도 형식) → 초록
LEVEL_TTL = 60

_ext = APIRouter()


def _demo():
    """{id: {"name","lat","lng","cap"}}  places 테이블에 있는 것만."""
    extra = PX._extra_places()
    return {pid: {**extra[pid], "cap": cap} for pid, cap in DEMO.items() if pid in extra}


# ---------------------------------------------------------------------------
# 종합 색 = 소음·온도·습도 중 가장 나쁜 색 (조도 제외)
# ---------------------------------------------------------------------------
LEVEL_FACTORS = ("noise", "temp", "humidity")
LEVELS = ("green", "yellow", "red")


def _score_factors(vals, ls):
    """score_ext._score_factors 대체. 반환 형식 같음: (level, worst, 항목별 색, 점수 dict)."""
    r = SS.sensory_score(vals.get("noise"), vals.get("temp"), vals.get("humidity"), light_score=None)
    lv = {}
    if vals.get("noise") is not None:
        lv["noise"] = SX._color_of_item(SS._one_sided(vals["noise"], SS.NOISE_BEST, SS.NOISE_WORST))
    if vals.get("temp") is not None:
        lv["temp"] = SX._color_of_item(SS._two_sided(vals["temp"], SS.TEMP_OK, SS.TEMP_ZERO))
    if vals.get("humidity") is not None:
        lv["humidity"] = SX._color_of_item(SS._two_sided(vals["humidity"], SS.HUM_OK, SS.HUM_ZERO))
    if ls is not None:
        lv["light"] = SX._color_of_item(ls)          # 표시용으로만 남김
    known = [lv[f] for f in LEVEL_FACTORS if f in lv]
    if not known:
        return "gray", [], lv, r
    rank = max(A.LEVEL_RANK[c] for c in known)
    level = LEVELS[rank]
    worst = [f for f in LEVEL_FACTORS if lv.get(f) and A.LEVEL_RANK[lv[f]] == rank] if rank > 0 else []
    return level, worst, lv, r


SX._score_factors = _score_factors


# ---------------------------------------------------------------------------
# 시연 값 (색 → 소음 dB, 실제 점수식으로 그 색이 나오는 범위 안에서)
# ---------------------------------------------------------------------------
_bands = {}


def _band(color):
    """시연 온습도·조도에서 그 색이 나오는 소음 범위 (lo, hi)."""
    if color not in _bands:
        xs = [x / 10 for x in range(400, 951)]
        # 종합 색과 소음 항목 색이 둘 다 그 색인 범위
        ok = [x for x in xs
              if _score_factors({"noise": x, "temp": DEMO_TEMP, "humidity": DEMO_HUM}, DEMO_LIGHT)[0] == color
              and SX._color_of_item(SS._one_sided(x, SS.NOISE_BEST, SS.NOISE_WORST)) == color]
        _bands[color] = (min(ok), max(ok)) if ok else (None, None)
    return _bands[color]


def _target(cap, h):
    return PATTERN[cap][CROWD[h]]


def _noise(place_id, cap, h):
    color = _target(cap, h)
    lo, hi = _band(color)
    if lo is None:
        return None
    if color == "green":
        lo = max(lo, hi - 8.0)           # 초록은 너무 조용하지 않게
    if color == "red":
        hi = min(hi, lo + 6.0)           # 빨강은 너무 시끄럽지 않게
    # 시간마다 조금씩 다르게 (범위의 25~75% 안)
    j = (sum(map(ord, place_id)) * 7 + h * 13) % 11 / 10.0
    return round(lo + (hi - lo) * (0.25 + 0.5 * j), 1)


def _demo_hour(place_id, d, h):
    p = _demo()[place_id]
    i = CROWD[h]
    vals = {"noise": _noise(place_id, p["cap"], h),
            "temp": DEMO_TEMP + 0.5 * i, "humidity": DEMO_HUM + 2.0 * i}
    srcs = {f: "demo" for f in vals}
    confs = {f: "low" for f in vals}
    e = SX._hour_entry(h, vals, srcs, confs, DEMO_LIGHT)
    e["factors"]["light"]["source"] = "demo"
    e["factors"]["light"]["confidence"] = "low"
    e["source"], e["confidence"] = "demo", "low"
    return e


def _demo_street(h):
    i = CROWD[h]
    pop = (400, 1300, 2200)[i]
    return {"hour": h, "population": float(pop), "population_source": "demo",
            "crowd": round(pop / 2200, 2), "crowd_level": ("low", "mid", "high")[i]}


def _weather_now():
    try:
        w = A._q("""SELECT obs_time, temp, humidity, rain_15m, rain_60m
                    FROM weather_now WHERE stn=108 ORDER BY obs_time DESC LIMIT 1""")
    except Exception:
        return None
    if not w:
        return None
    w = w[0]
    return {"obs_time": str(w["obs_time"]), "temp": A._num(w["temp"]),
            "humidity": A._num(w["humidity"]), "rain_15m": A._num(w["rain_15m"]),
            "rain_60m": A._num(w["rain_60m"])}


def _topic(name):
    for ch in reversed(name):
        if "가" <= ch <= "힣":
            return name + ("은" if (ord(ch) - 0xAC00) % 28 else "는")
    return name + "는"


# ---------------------------------------------------------------------------
# /places : level 포함, 필드 통일
# ---------------------------------------------------------------------------
def _sensor_level(pid):
    def load():
        try:
            ind = A.now(pid)["indoor"]
            return ind["level"]
        except Exception:
            return "gray"
    return A._cache.get(f"place_level:{pid}", LEVEL_TTL, load)


@_ext.get("/places")
def list_places():
    items = PX.list_places()
    demo = _demo()
    nk = A._now_kst()
    real, demos, rest = [], [], []
    for it in items:
        pid = it["id"]
        it["has_sensor"] = pid in A.PLACES
        it["is_demo"] = pid in demo
        if it["has_sensor"]:
            it["level"], it["level_source"] = _sensor_level(pid), "sensor"
            real.append(it)
        elif it["is_demo"]:
            it["level"], it["level_source"] = _demo_hour(pid, nk.date(), nk.hour)["level"], "demo"
            demos.append(it)
        else:
            it["level"], it["level_source"] = "gray", "none"
            rest.append(it)
    for it in real + demos + rest:
        it["level_time"] = nk.strftime("%Y-%m-%dT%H:00:00")
    return real + demos + rest     # 실측 → 시연 → 나머지 순서


# ---------------------------------------------------------------------------
# 시연 카페 /indoor /street, 센서 없는 카페 /now
# ---------------------------------------------------------------------------
@_ext.get("/places/{place_id}/indoor")
def indoor(place_id: str, date: str = Query(None, description="YYYY-MM-DD (KST), 생략 시 오늘")):
    if place_id not in _demo():
        return PX.indoor(place_id, date)
    d = A._parse_date(date)
    return {"place_id": place_id, "is_demo": True, "has_sensor": False,
            "date": d.isoformat(), "day_type": A._day_type(d),
            "nearest_sensor_place": PX._nearest_sensor(_demo()[place_id]),
            "hours": [_demo_hour(place_id, d, h) for h in range(24)]}


@_ext.get("/places/{place_id}/street")
def street(place_id: str, date: str = Query(None, description="YYYY-MM-DD (KST), 생략 시 오늘")):
    if place_id not in _demo():
        return PX.street(place_id, date)
    d = A._parse_date(date)
    try:
        weather = A._weather_for_day(d)
    except Exception:
        weather = {h: None for h in range(24)}
    return {"place_id": place_id, "is_demo": True, "has_sensor": False,
            "date": d.isoformat(), "day_type": A._day_type(d),
            "hours": [{**_demo_street(h), "weather": weather.get(h)} for h in range(24)]}


@_ext.get("/places/{place_id}/now")
def now(place_id: str):
    if place_id in A.PLACES:
        out = A.now(place_id)
        out["is_demo"], out["has_sensor"], out["data_available"] = False, True, True
        return out
    demo = _demo()
    p = demo.get(place_id) or PX._extra_places().get(place_id)
    if not p:
        raise HTTPException(404, f"unknown place_id: {place_id}")
    nk = A._now_kst()
    near = PX._nearest_sensor(p)
    if place_id in demo:
        e = _demo_hour(place_id, nk.date(), nk.hour)
        factors = {f: {"value": v["value"], "level": v["level"]} for f, v in e["factors"].items()}
        st = _demo_street(nk.hour)
        if e["level"] == "green":
            summary = f"{_topic(p['name'])} 지금 쾌적해요. (시연용 가정값)"
        else:
            names = ", ".join(A.FACTOR_KO[f] for f in e["worst_factors"]) or "소음"
            summary = (f"{_topic(p['name'])} 지금 {names} 때문에 {A.LEVEL_KO[e['level']]}이에요. "
                       f"(시연용 가정값)")
        return {
            "place_id": place_id, "is_demo": True, "has_sensor": False, "data_available": True,
            "time": nk.strftime("%Y-%m-%dT%H:%M:%S"),
            "indoor": {"level": e["level"], "worst_factors": e["worst_factors"], "factors": factors,
                       "score": e["score"], "grade": e["grade"],
                       "source": "demo", "confidence": "low", "measured_at": None, "stale": False},
            "street": {"population": st["population"], "population_source": "demo",
                       "crowd_level": st["crowd_level"], "weather": _weather_now()},
            "nearest_sensor_place": near,
            "summary_ko": summary,
        }
    gray = {"value": None, "level": "gray"}
    s = f"{_topic(p['name'])} 아직 센서가 없어서 상태를 알 수 없어요."
    if near:
        s += f" 가장 가까운 {near['name']}(약 {near['distance_m']}m) 정보를 참고해 주세요."
    return {
        "place_id": place_id, "is_demo": False, "has_sensor": False, "data_available": False,
        "time": nk.strftime("%Y-%m-%dT%H:%M:%S"),
        "indoor": {"level": "gray", "worst_factors": [], "factors": {f: dict(gray) for f in A.FACTORS},
                   "score": None, "grade": None,
                   "source": "none", "confidence": None, "measured_at": None, "stale": True},
        "street": {"population": None, "population_source": "none", "crowd_level": None,
                   "weather": _weather_now()},
        "nearest_sensor_place": near,
        "summary_ko": s,
    }


@_ext.get("/thresholds")
def thresholds():
    return {
        "rule": "종합 level = 소음·온도·습도 항목 색 중 가장 나쁜 색 (조도는 색 계산에서 제외, 값 없는 항목은 무시)",
        "item_color": "항목 점수(0~10) 7.5 이상 green, 5.5 이상 yellow, 그 아래 red",
        "noise_db": {"score_10_at_or_below": SS.NOISE_BEST, "score_0_at_or_above": SS.NOISE_WORST},
        "temp_c": {"score_10_range": list(SS.TEMP_OK), "score_0_at": list(SS.TEMP_ZERO)},
        "humidity_pct": {"score_10_range": list(SS.HUM_OK), "score_0_at": list(SS.HUM_ZERO)},
        "light": "factors.light 는 원탭 제보 조도점수(0~10). 응답에는 남지만 색 계산에는 안 씀",
        "score": "score/grade 는 조도를 뺀 가중 점수(소음 0.76, 온습도 0.24), 참고용",
    }


@_ext.get("/demo/places")
def demo_places():
    """시연 카페 목록 확인용."""
    nk = A._now_kst()
    return [{"id": pid, "name": p["name"], "cap": p["cap"],
             "levels_today": [_target(p["cap"], h) for h in range(24)],
             "level_now": _demo_hour(pid, nk.date(), nk.hour)["level"]}
            for pid, p in _demo().items()]


# places_ext·app_api 의 같은 경로보다 먼저 매칭되도록 맨 앞에 끼워 넣음
A.router.routes[:0] = _ext.routes
