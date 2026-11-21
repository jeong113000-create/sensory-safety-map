"""
감각안전지도 앱 API v2  (카페 안 / 길거리 분리)

main.py 는 그대로 두면 됨:
    from app_api import router, configure
    configure(RDS_HOST, "admin", RDS_PASSWORD, "sensory_db"); app.include_router(router)

엔드포인트
    GET /places                          카페 목록
    GET /places/{id}/indoor?date=        카페 안 신호등 (소음·온도·습도·조도, 가장 나쁜 색)
    GET /places/{id}/street?date=        길거리 참고 정보 (생활인구 혼잡도 + 날씨)
    GET /places/{id}/now                 지금 상태 (음성 에이전트용, indoor+street 한 번에)
    GET /thresholds                      지금 쓰는 신호등 기준값

시연용 카페(is_demo=true, cafe-001~004)는 DB를 전혀 건드리지 않고 이 파일 안에서 값을 만들어 냄.
(sensor_data / predictions 에 가짜 행을 넣으면 백분위·학습 데이터가 오염되므로)

시간 기준: sensor_data 만 UTC, 나머지 테이블과 API 입출력은 전부 KST.
"""

import datetime as dt
import threading
import time
from collections import defaultdict

import pymysql
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
_DB = {}


def configure(host, user, password, database):
    _DB.update(host=host, user=user, password=password, database=database)


PLACES = {
    "cafe-A": {"name": "포틀리에", "lat": 37.6089377144737, "lng": 127.004746150444,
               "env_sensor": "cafe-A", "sound_sensor": "cafe-A-sound"},
    "cafe-B": {"name": "정릉동커피", "lat": 37.6087137891644, "lng": 127.007122998329,
               "env_sensor": "cafe-B", "sound_sensor": "cafe-B-sound"},
}

# 시연용 카페 (국민대 주변). cap = 하루 중 가장 나쁠 때의 색, None 이면 측정값 없음(gray)
DEMO_PLACES = {
    "cafe-001": {"name": "조용한 숲 카페",      "lat": 37.6118, "lng": 126.9952, "cap": "green"},
    "cafe-002": {"name": "정릉 브루잉",         "lat": 37.6097, "lng": 126.9985, "cap": "yellow"},
    "cafe-003": {"name": "북악 스터디 라운지",  "lat": 37.6125, "lng": 126.9991, "cap": "red"},
    "cafe-004": {"name": "새로 생긴 골목 카페", "lat": 37.6092, "lng": 126.9948, "cap": None},
}
DEMO_PCT = {"noise": {"p50": 50.0, "p80": 70.0, "source": "demo", "hours_used": None},
            "light": {"p50": 500.0, "p80": 800.0, "source": "demo", "hours_used": None}}

# 온도·습도: (초록 하한, 초록 상한, 노랑 하한, 노랑 상한)
TEMP_BANDS = (20.0, 26.0, 18.0, 28.0)
HUM_BANDS = (40.0, 60.0, 30.0, 70.0)

# 소음·조도: percentiles.sql 결과를 넣으면 고정값 사용.
# None 이면 서버가 DB에서 같은 규칙(영업시간, 70% 완결 시간 평균)으로 자동 계산.
FIXED_PCT = {
    "cafe-A": {"noise": None, "light": None},   # 예: {"noise": (p50, p80), "light": (p50, p80)}
    "cafe-B": {"noise": None, "light": None},
}
PCT_HOURS = (9, 22)          # 자동 계산 시 사용하는 영업시간 (KST, 양끝 포함)

MIN_N = 252                  # 360개 * 0.7
CACHE_SEC = 600              # 시간 집계 캐시 10분
NOW_WINDOW_MIN = 10          # /now: 최근 10분 평균
STALE_MIN = 5                # 마지막 측정이 5분보다 오래되면 stale

FACTORS = ("noise", "temp", "humidity", "light")
FACTOR_KO = {"noise": "소음", "temp": "온도", "humidity": "습도", "light": "조도"}
LEVEL_RANK = {"green": 0, "yellow": 1, "red": 2}
LEVEL_KO = {"green": "초록", "yellow": "노랑", "red": "빨강", "gray": "정보 없음"}
SKY_KO = {1: "맑음", 3: "구름많음", 4: "흐림"}
PTY_KO = {0: "없음", 1: "비", 2: "비/눈", 3: "눈", 5: "빗방울", 6: "빗방울눈날림", 7: "눈날림"}


# ---------------------------------------------------------------------------
# DB / 시간 유틸
# ---------------------------------------------------------------------------
def _q(sql, args=()):
    conn = pymysql.connect(charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                           connect_timeout=5, **_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def _now_kst():
    return dt.datetime.utcnow() + dt.timedelta(hours=9)


def _num(x, nd=1):
    if x is None:
        return None
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _place(place_id):
    p = PLACES.get(place_id) or DEMO_PLACES.get(place_id)
    if not p:
        raise HTTPException(404, f"unknown place_id: {place_id}")
    return p


def _parse_date(s):
    if not s:
        return _now_kst().date()
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "date 형식은 YYYY-MM-DD")


class _Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}

    def get(self, key, ttl, fn):
        with self.lock:
            hit = self.data.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        val = fn()
        with self.lock:
            self.data[key] = (time.time(), val)
        return val


_cache = _Cache()


def _holidays():
    def load():
        try:
            return {r["date"] for r in _q("SELECT date FROM holidays")}
        except Exception:
            return set()
    return _cache.get("holidays", 3600, load)


def _day_type(d):
    return "weekend" if d.weekday() >= 5 or d in _holidays() else "weekday"


# ---------------------------------------------------------------------------
# 카페 안 (센서)
# ---------------------------------------------------------------------------
def _sensor_hourly():
    """{place_id: {(date, hour): {factor: value}}}  70% 완결 시간만, KST."""
    def load():
        ids = []
        for p in PLACES.values():
            ids += [p["env_sensor"], p["sound_sensor"]]
        rows = _q(
            f"""
            SELECT sensor_id,
                   DATE(DATE_ADD(created_at, INTERVAL 9 HOUR)) AS d,
                   HOUR(DATE_ADD(created_at, INTERVAL 9 HOUR)) AS h,
                   COUNT(temperature) AS n_t, AVG(temperature) AS t,
                   COUNT(humidity)    AS n_h, AVG(humidity)    AS hu,
                   COUNT(light)       AS n_l, AVG(light)       AS l,
                   COUNT(sound)       AS n_s, AVG(sound)       AS s
            FROM sensor_data
            WHERE sensor_id IN ({",".join(["%s"] * len(ids))})
            GROUP BY sensor_id, d, h
            """, ids)
        by_sensor = {p: {} for p in PLACES}
        sensor_to_place = {}
        for pid, p in PLACES.items():
            sensor_to_place[p["env_sensor"]] = pid
            sensor_to_place[p["sound_sensor"]] = pid
        for r in rows:
            pid = sensor_to_place[r["sensor_id"]]
            slot = by_sensor[pid].setdefault((r["d"], int(r["h"])), {})
            for n_key, v_key, f in (("n_t", "t", "temp"), ("n_h", "hu", "humidity"),
                                    ("n_l", "l", "light"), ("n_s", "s", "noise")):
                if r[n_key] >= MIN_N:
                    slot[f] = float(r[v_key])
        return by_sensor
    return _cache.get("sensor_hourly", CACHE_SEC, load)


def _pct(values, p):
    """nearest-rank 백분위 (percentiles.sql 과 같은 방식)."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, -(-int(round(p * 100)) * len(s) // 100))  # ceil(p*N)
    return s[k - 1]


def _thresholds(place_id):
    out = {}
    hourly = _sensor_hourly()[place_id]
    for f in ("noise", "light"):
        fixed = FIXED_PCT.get(place_id, {}).get(f)
        if fixed:
            out[f] = {"p50": fixed[0], "p80": fixed[1], "source": "fixed", "hours_used": None}
            continue
        vals = [v[f] for (d, h), v in hourly.items()
                if PCT_HOURS[0] <= h <= PCT_HOURS[1] and f in v]
        out[f] = {"p50": _num(_pct(vals, 0.5)), "p80": _num(_pct(vals, 0.8)),
                  "source": "auto", "hours_used": len(vals)}
    return out


def _level(factor, value, th):
    if value is None:
        return "gray"
    if factor in ("temp", "humidity"):
        g_lo, g_hi, y_lo, y_hi = TEMP_BANDS if factor == "temp" else HUM_BANDS
        if g_lo <= value <= g_hi:
            return "green"
        if y_lo <= value <= y_hi:
            return "yellow"
        return "red"
    t = th.get(factor) or {}
    if t.get("p50") is None:
        return "gray"
    if value <= t["p50"]:
        return "green"
    if value <= t["p80"]:
        return "yellow"
    return "red"


def _overall(factors):
    """가장 나쁜 색. gray(정보 없음)는 무시, 전부 gray 면 gray."""
    known = {f: v["level"] for f, v in factors.items() if v["level"] != "gray"}
    if not known:
        return "gray", []
    worst = max(known.values(), key=LEVEL_RANK.get)
    if worst == "green":
        return "green", []
    return worst, [f for f in FACTORS if known.get(f) == worst]


def _predictions(place_id, d):
    try:
        rows = _q("""SELECT hour, noise, temp, humidity, light, confidence
                     FROM predictions WHERE place_id=%s AND date=%s""", (place_id, d))
    except Exception:
        return {}   # 테이블이 아직 없음
    return {int(r["hour"]): r for r in rows}


def _indoor_hours(place_id, d):
    th = _thresholds(place_id)
    hourly = _sensor_hourly()[place_id]
    now = _now_kst()
    cur_slot = (now.date(), now.hour)
    dtype = _day_type(d)

    # 같은 요일유형(평일/주말·공휴일) × 시간의 과거 평균 = baseline
    base_sum = defaultdict(lambda: defaultdict(list))
    for (hd, hh), vals in hourly.items():
        if hd != d and _day_type(hd) == dtype:
            for f, v in vals.items():
                base_sum[hh][f].append(v)

    preds = _predictions(place_id, d)
    hours = []
    for h in range(24):
        slot = (d, h)
        is_past = slot < cur_slot
        factors = {}
        for f in FACTORS:
            value, src, conf = None, "none", None
            if is_past:
                if f in hourly.get(slot, {}):
                    value, src, conf = hourly[slot][f], "measured", "high"
            else:
                pr = preds.get(h)
                if pr and pr.get(f) is not None:
                    value, src, conf = float(pr[f]), "model", pr.get("confidence") or "high"
                elif base_sum[h][f]:
                    xs = base_sum[h][f]
                    value, src = sum(xs) / len(xs), "baseline"
                    conf = "high" if len(xs) >= 3 else "low"
            factors[f] = {"value": _num(value), "level": _level(f, value, th),
                          "source": src, "confidence": conf}
        level, worst = _overall(factors)
        srcs = [v["source"] for v in factors.values() if v["source"] != "none"]
        confs = [v["confidence"] for v in factors.values() if v["confidence"]]
        hours.append({
            "hour": h,
            "level": level,
            "worst_factors": worst,
            "source": (srcs and max(set(srcs), key=srcs.count)) or "none",
            "confidence": ("low" if "low" in confs else "high") if confs else None,
            "factors": factors,
        })
    return hours


@router.get("/places")
def list_places():
    real = [{"id": k, "name": v["name"], "lat": v["lat"], "lng": v["lng"], "is_demo": False}
            for k, v in PLACES.items()]
    demo = [{"id": k, "name": v["name"], "lat": v["lat"], "lng": v["lng"], "is_demo": True}
            for k, v in DEMO_PLACES.items()]
    return real + demo


@router.get("/places/{place_id}/indoor")
def indoor(place_id: str, date: str = Query(None, description="YYYY-MM-DD (KST), 생략 시 오늘")):
    _place(place_id)
    d = _parse_date(date)
    if place_id in DEMO_PLACES:
        return {"place_id": place_id, "is_demo": True, "date": d.isoformat(),
                "day_type": _day_type(d), "hours": [_demo_indoor_hour(place_id, h) for h in range(24)]}
    return {"place_id": place_id, "is_demo": False, "date": d.isoformat(), "day_type": _day_type(d),
            "hours": _indoor_hours(place_id, d)}


# ---------------------------------------------------------------------------
# 길거리 (생활인구 + 날씨)
# ---------------------------------------------------------------------------
def _population_all():
    def load():
        rows = _q("SELECT place_id, date, hour, population FROM population_250m")
        out = defaultdict(dict)
        for r in rows:
            if r["population"] is not None:
                out[r["place_id"]][(r["date"], int(r["hour"]))] = float(r["population"])
        return out
    return _cache.get("population", 3600, load)


def _crowd_info(place_id, pop_by_slot):
    vals = list(pop_by_slot.values())
    return {"p50": _pct(vals, 0.5), "p80": _pct(vals, 0.8), "max": max(vals) if vals else None}


def _crowd_level(pop, ci):
    if pop is None or ci["p50"] is None:
        return None
    return "low" if pop <= ci["p50"] else ("mid" if pop <= ci["p80"] else "high")


def _population_for(place_id, d, h, pops, dtype):
    if (d, h) in pops:
        return pops[(d, h)], "actual"
    xs = [v for (pd, ph), v in pops.items() if ph == h and _day_type(pd) == dtype]
    if xs:
        return sum(xs) / len(xs), "baseline"
    return None, "none"


def _weather_for_day(d):
    obs = {int(r["hour"]): r for r in _q(
        "SELECT hour, temp, humidity, rain FROM weather_hourly WHERE date=%s AND stn=108", (d,))}
    start = dt.datetime.combine(d, dt.time(0))
    fc_rows = _q("""
        SELECT f.fcst_time, f.temp, f.humidity, f.rain, f.pty, f.sky
        FROM weather_ultra_fcst f
        JOIN (SELECT fcst_time, MAX(base_time) AS bt FROM weather_ultra_fcst
              WHERE fcst_time >= %s AND fcst_time < %s GROUP BY fcst_time) m
          ON f.fcst_time = m.fcst_time AND f.base_time = m.bt""",
        (start, start + dt.timedelta(days=1)))
    fc = {r["fcst_time"].hour: r for r in fc_rows}
    try:  # 단기예보 (약 3일). 테이블이 아직 없으면 건너뜀
        vf_rows = _q("""
            SELECT f.fcst_time, f.temp, f.humidity, f.rain, f.pop, f.pty, f.sky
            FROM weather_vilage_fcst f
            JOIN (SELECT fcst_time, MAX(base_time) AS bt FROM weather_vilage_fcst
                  WHERE fcst_time >= %s AND fcst_time < %s GROUP BY fcst_time) m
              ON f.fcst_time = m.fcst_time AND f.base_time = m.bt""",
            (start, start + dt.timedelta(days=1)))
    except Exception:
        vf_rows = []
    vf = {r["fcst_time"].hour: r for r in vf_rows}
    nk = _now_kst()
    live = None
    if d == nk.date():
        w = _q("""SELECT temp, humidity, rain_60m FROM weather_now
                  WHERE stn=108 ORDER BY obs_time DESC LIMIT 1""")
        live = w[0] if w else None
    out = {}
    for h in range(24):
        if h in obs:
            r = obs[h]
            out[h] = {"temp": _num(r["temp"]), "humidity": _num(r["humidity"]),
                      "rain": _num(r["rain"]), "sky": None, "pty": None, "source": "observed"}
        elif h in fc:
            r = fc[h]
            out[h] = {"temp": _num(r["temp"]), "humidity": _num(r["humidity"]),
                      "rain": r["rain"] if isinstance(r["rain"], str) else _num(r["rain"]),
                      "sky": SKY_KO.get(_int(r["sky"])), "pty": PTY_KO.get(_int(r["pty"])),
                      "source": "forecast"}
        elif live and h == nk.hour:
            out[h] = {"temp": _num(live["temp"]), "humidity": _num(live["humidity"]),
                      "rain": _num(live["rain_60m"]), "sky": None, "pty": None, "source": "now"}
        elif h in vf:
            r = vf[h]
            out[h] = {"temp": _num(r["temp"]), "humidity": _num(r["humidity"]),
                      "rain": _num(r["rain"]), "pop": _int(r["pop"]),
                      "sky": SKY_KO.get(_int(r["sky"])), "pty": PTY_KO.get(_int(r["pty"])),
                      "source": "forecast_3day"}
        else:
            out[h] = None   # 예보 범위 밖
    return out


def _int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


@router.get("/places/{place_id}/street")
def street(place_id: str, date: str = Query(None, description="YYYY-MM-DD (KST), 생략 시 오늘")):
    _place(place_id)
    d = _parse_date(date)
    dtype = _day_type(d)
    if place_id in DEMO_PLACES:
        weather = _weather_for_day(d)
        return {"place_id": place_id, "is_demo": True, "date": d.isoformat(), "day_type": dtype,
                "hours": [{**_demo_street(h), "weather": weather[h]} for h in range(24)]}
    pops = _population_all().get(place_id, {})
    ci = _crowd_info(place_id, pops)
    weather = _weather_for_day(d)
    hours = []
    for h in range(24):
        pop, psrc = _population_for(place_id, d, h, pops, dtype)
        hours.append({
            "hour": h,
            "population": _num(pop, 0),
            "population_source": psrc,
            "crowd": _num(min(pop / ci["max"], 1.0), 2) if pop is not None and ci["max"] else None,
            "crowd_level": _crowd_level(pop, ci),
            "weather": weather[h],
        })
    return {"place_id": place_id, "is_demo": False, "date": d.isoformat(), "day_type": dtype,
            "hours": hours}


# ---------------------------------------------------------------------------
# 지금 상태 (음성 에이전트용)
# ---------------------------------------------------------------------------
@router.get("/places/{place_id}/now")
def now(place_id: str):
    p = _place(place_id)
    nk = _now_kst()
    if place_id in DEMO_PLACES:
        return _demo_now(place_id, p, nk)
    th = _thresholds(place_id)

    rows = _q("""
        SELECT sensor_id, MAX(created_at) AS last_at,
               AVG(temperature) AS t, AVG(humidity) AS hu, AVG(light) AS l, AVG(sound) AS s
        FROM sensor_data
        WHERE sensor_id IN (%s, %s)
          AND created_at >= UTC_TIMESTAMP() - INTERVAL %s MINUTE
        GROUP BY sensor_id""", (p["env_sensor"], p["sound_sensor"], NOW_WINDOW_MIN))
    by = {r["sensor_id"]: r for r in rows}
    env, snd = by.get(p["env_sensor"], {}), by.get(p["sound_sensor"], {})
    vals = {"temp": env.get("t"), "humidity": env.get("hu"),
            "light": env.get("l"), "noise": snd.get("s")}
    factors = {}
    for f in FACTORS:
        v = None if vals[f] is None else float(vals[f])
        factors[f] = {"value": _num(v), "level": _level(f, v, th)}
    level, worst = _overall(factors)

    lasts = [r["last_at"] for r in rows if r["last_at"]]
    last_kst = (max(lasts) + dt.timedelta(hours=9)) if lasts else None
    stale = last_kst is None or (nk - last_kst) > dt.timedelta(minutes=STALE_MIN)

    w = _q("""SELECT obs_time, temp, humidity, rain_15m, rain_60m
              FROM weather_now WHERE stn=108 ORDER BY obs_time DESC LIMIT 1""")
    weather = None
    if w:
        w = w[0]
        weather = {"obs_time": str(w["obs_time"]), "temp": _num(w["temp"]),
                   "humidity": _num(w["humidity"]), "rain_15m": _num(w["rain_15m"]),
                   "rain_60m": _num(w["rain_60m"])}

    pops = _population_all().get(place_id, {})
    ci = _crowd_info(place_id, pops)
    # 생활인구는 약 4일 늦게 공개 → 지금은 항상 같은 요일유형·시간 평균
    xs = [v for (pd, ph), v in pops.items() if ph == nk.hour and _day_type(pd) == _day_type(nk.date())]
    pop = sum(xs) / len(xs) if xs else None

    return {
        "place_id": place_id,
        "is_demo": False,
        "time": nk.strftime("%Y-%m-%dT%H:%M:%S"),
        "indoor": {"level": level, "worst_factors": worst, "factors": factors,
                   "measured_at": last_kst.strftime("%Y-%m-%dT%H:%M:%S") if last_kst else None,
                   "stale": stale},
        "street": {"population": _num(pop, 0), "population_source": "baseline" if pop else "none",
                   "crowd_level": _crowd_level(pop, ci), "weather": weather},
        "summary_ko": _summary(p["name"], level, worst, factors, stale),
    }


def _topic(name):
    """이름 끝 받침에 맞춰 은/는."""
    for ch in reversed(name):
        if "가" <= ch <= "힣":
            return name + ("은" if (ord(ch) - 0xAC00) % 28 else "는")
    return name + "는"


def _summary(name, level, worst, factors, stale):
    name = _topic(name)
    if stale or level == "gray":
        return f"{name} 지금 센서 정보가 없어서 상태를 알 수 없어요."
    if level == "green":
        return f"{name} 지금 쾌적해요."
    names = ", ".join(FACTOR_KO[f] for f in worst)
    tone = "조금 신경 쓰일 수 있어요" if level == "yellow" else "자극이 강한 편이에요"
    return f"{name} 지금 {names} 때문에 {LEVEL_KO[level]}이에요. {tone}."


@router.get("/thresholds")
def thresholds():
    return {
        "temp": {"green": TEMP_BANDS[:2], "yellow": TEMP_BANDS[2:]},
        "humidity": {"green": HUM_BANDS[:2], "yellow": HUM_BANDS[2:]},
        "percentile_rule": "noise/light: <=p50 green, <=p80 yellow, 초과 red",
        "places": {pid: _thresholds(pid) for pid in PLACES},
        "demo_places": DEMO_PCT,
    }


# ---------------------------------------------------------------------------
# 시연용 카페 값 생성 (DB 안 씀)
#   혼잡 패턴: 오전 한산 → 점심 혼잡 → 오후 중간 → 저녁 혼잡, 밤은 거의 비어 있음
#   cap 색은 혼잡 최고점(점심·저녁)에서만 나오고, 색 판정은 실측 카페와 같은 _level/_overall 사용
# ---------------------------------------------------------------------------
def _demo_intensity(h):
    if 11 <= h < 14 or 17 <= h < 21:
        return 0.9          # 점심·저녁 혼잡
    if 14 <= h < 17:
        return 0.5          # 오후 중간
    if 7 <= h < 11:
        return 0.2          # 오전 한산
    return 0.05             # 밤·새벽


def _demo_values(cap, h):
    if cap is None:
        return {f: None for f in FACTORS}
    i = _demo_intensity(h)
    base, span = {"green": (30, 15), "yellow": (35, 30), "red": (40, 40)}[cap]
    return {"noise": base + span * i,                 # green 최대 43.5 / yellow 62 / red 76
            "temp": 22.0 + 2.0 * i,                   # 항상 초록 범위
            "humidity": 45.0 + 8.0 * i,               # 항상 초록 범위
            "light": 300.0 + 150.0 * i}               # 항상 초록 범위


def _demo_factors(cap, h, with_meta):
    vals = _demo_values(cap, h)
    out = {}
    for f in FACTORS:
        v = vals[f]
        item = {"value": _num(v), "level": _level(f, v, DEMO_PCT)}
        if with_meta:
            item.update(source="demo" if v is not None else "none",
                        confidence="high" if v is not None else None)
        out[f] = item
    return out


def _demo_indoor_hour(place_id, h):
    cap = DEMO_PLACES[place_id]["cap"]
    factors = _demo_factors(cap, h, with_meta=True)
    level, worst = _overall(factors)
    return {"hour": h, "level": level, "worst_factors": worst,
            "source": "demo" if cap else "none",
            "confidence": "high" if cap else None, "factors": factors}


def _demo_street(h):
    i = _demo_intensity(h)
    pop = 300 + 2000 * i
    return {"hour": h, "population": float(round(pop)), "population_source": "demo",
            "crowd": round(pop / 2100, 2),
            "crowd_level": "high" if i >= 0.9 else ("mid" if i >= 0.5 else "low")}


def _demo_now(place_id, p, nk):
    cap = p["cap"]
    factors = _demo_factors(cap, nk.hour, with_meta=False)
    level, worst = _overall(factors)
    w = _q("""SELECT obs_time, temp, humidity, rain_15m, rain_60m
              FROM weather_now WHERE stn=108 ORDER BY obs_time DESC LIMIT 1""")
    weather = None
    if w:
        w = w[0]
        weather = {"obs_time": str(w["obs_time"]), "temp": _num(w["temp"]),
                   "humidity": _num(w["humidity"]), "rain_15m": _num(w["rain_15m"]),
                   "rain_60m": _num(w["rain_60m"])}
    st = _demo_street(nk.hour)
    stale = cap is None
    return {
        "place_id": place_id,
        "is_demo": True,
        "time": nk.strftime("%Y-%m-%dT%H:%M:%S"),
        "indoor": {"level": level, "worst_factors": worst, "factors": factors,
                   "measured_at": None if stale else nk.strftime("%Y-%m-%dT%H:%M:%S"),
                   "stale": stale},
        "street": {"population": st["population"], "population_source": "demo",
                   "crowd_level": st["crowd_level"], "weather": weather},
        "summary_ko": _summary(p["name"], level, worst, factors, stale),
    }
