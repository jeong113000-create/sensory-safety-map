"""
신호등 계산을 sensory_score(소음·온습도 + 원탭 조도 제보)로 바꾸는 연결 파일.

- 조도 센서 값은 신호등 색에 안 씀. 응답의 factors.light 는 제보 기반 조도점수로 채움.
- POST /report, GET /report/summary 를 앱 API 에 붙임 (main.py 수정 없음).
- DB 접속은 앱 API 가 이미 쓰는 정보(app_api.configure)를 그대로 씀. 환경변수 필요 없음.
- user_reports 표는 첫 요청 때 자동으로 만듦 (CREATE TABLE IF NOT EXISTS).
- 11/18: 추정 카페(센서 없음)는 소음·온도 원탭 제보를 신호등 색에 섞음 (측정 카페는 안 섞음).
  제보로 섞은 항목점수를 같은 점수가 나오는 dB·℃로 바꿔 _score_factors에 넘김
  (demo_ext가 _score_factors를 2인자 함수로 바꿔 끼우므로 인자를 늘리지 않음). 화면 value는 원래 추정값.

설치: app_api.py 맨 끝 `import places_ext` 아래에 `import score_ext` 한 줄.
되돌리기: 그 한 줄만 지우고 재시작.
"""

import datetime as dt
import threading
from collections import defaultdict

import pymysql

import app_api as A
import places_ext as PX
import sensory_score as SS
import user_reports as UR

LIGHT_TTL = 60   # 제보 집계 캐시 (초)

# ---------------------------------------------------------------------------
# DB 연결: 앱 API 접속 정보 재사용 + 표 자동 생성
# ---------------------------------------------------------------------------
_table_ok = False
_table_lock = threading.Lock()


def _connect():
    global _table_ok
    conn = pymysql.connect(charset="utf8mb4", connect_timeout=5, **A._DB)
    if not _table_ok:
        with _table_lock:
            if not _table_ok:
                with conn.cursor() as cur:
                    cur.execute(UR.CREATE_SQL)
                conn.commit()
                _table_ok = True
    return conn


UR.connect = _connect


def _light_score(place_id, hour):
    """제보 조도점수(0~10) 또는 None. 같은 시간대(낮/저녁) 기준, 1분 캐시."""
    band = "all"

    def load():
        try:
            conn = _connect()
        except Exception:
            return None
        try:
            return UR.light_score_for(conn, place_id, hour)
        except Exception:
            return None
        finally:
            conn.close()
    return A._cache.get(f"light:{place_id}:{band}", LIGHT_TTL, load)


# ---------------------------------------------------------------------------
# 색 계산
# ---------------------------------------------------------------------------
def _color_of_item(score10):
    return SS.COLOR_OF_GRADE[SS.grade_of(10.0 * score10)]


def _score_factors(vals, ls):
    """vals: {"noise","temp","humidity"} 값(없으면 None), ls: 조도점수 or None.
    -> (level, worst_factors, per-factor level, score dict)"""
    r = SS.sensory_score(vals.get("noise"), vals.get("temp"), vals.get("humidity"), light_score=ls)
    lv = {}
    if vals.get("noise") is not None:
        lv["noise"] = _color_of_item(SS._one_sided(vals["noise"], SS.NOISE_BEST, SS.NOISE_WORST))
    if vals.get("temp") is not None:
        lv["temp"] = _color_of_item(SS._two_sided(vals["temp"], SS.TEMP_OK, SS.TEMP_ZERO))
    if vals.get("humidity") is not None:
        lv["humidity"] = _color_of_item(SS._two_sided(vals["humidity"], SS.HUM_OK, SS.HUM_ZERO))
    if ls is not None:
        lv["light"] = _color_of_item(ls)
    level = r["color"]
    worst = []
    if level != "gray" and level != "green":
        rank = max(A.LEVEL_RANK[c] for c in lv.values())
        if rank > 0:
            worst = [f for f in A.FACTORS if lv.get(f) and A.LEVEL_RANK[lv[f]] == rank]
    return level, worst, lv, r


def _pack(f, value, lv, src, conf):
    return {"value": A._num(value), "level": lv.get(f, "gray"), "source": src, "confidence": conf}


def _light_factor(ls, lv):
    if ls is None:
        return {"value": None, "level": "gray", "source": "none", "confidence": None}
    return {"value": ls, "level": lv["light"], "source": "report", "confidence": "high"}


def _with_reports(vals, nr, tr):
    """추정값 + 제보(3건 이상)를 섞은 항목점수를, 같은 점수가 나오는 소음 dB·온도 ℃로 바꾼 값 사본과 섞은 제보 수."""
    sv, used = dict(vals), {}
    n = sum((nr or {}).values())
    if n >= SS.LIGHT_MIN_REPORTS:
        x = vals.get("noise")
        est = SS._one_sided(x, SS.NOISE_BEST, SS.NOISE_WORST) if x is not None else None
        sc = SS.blend_with_reports(est, nr, SS.NOISE_REPORT_SCORE)
        sv["noise"] = SS.NOISE_WORST - sc * (SS.NOISE_WORST - SS.NOISE_BEST) / 10.0
        used["noise"] = n
    n = sum((tr or {}).values())
    if n >= SS.LIGHT_MIN_REPORTS:
        x = vals.get("temp")
        est = SS._two_sided(x, SS.TEMP_OK, SS.TEMP_ZERO) if x is not None else None
        sc = SS.blend_with_reports(est, tr, SS.TEMP_REPORT_SCORE)
        lo, hi = SS.TEMP_OK
        cold, hot = tr.get("cold", 0), tr.get("hot", 0)
        if cold != hot:
            side = "cold" if cold > hot else "hot"
        else:
            side = "hot" if (x is not None and x > hi) else "cold"
        if sc >= 10.0:
            sv["temp"] = x if (x is not None and lo <= x <= hi) else (lo + hi) / 2
        elif side == "cold":
            sv["temp"] = lo - (10.0 - sc) / 10.0 * (lo - SS.TEMP_ZERO[0])
        else:
            sv["temp"] = hi + (10.0 - sc) / 10.0 * (SS.TEMP_ZERO[1] - hi)
        used["temp"] = n
    return sv, used


def _hour_entry(h, vals, srcs, confs, ls, nr=None, tr=None):
    # 센서·모델 값이 하나도 없는 시간은 제보만으로 색을 내지 않음 (gray 유지)
    used_rep = {}
    if all(v is None for v in vals.values()):
        level, worst, lv, r = "gray", [], ({"light": _color_of_item(ls)} if ls is not None else {}), \
            {"score": None, "grade": None}
    else:
        sv, used_rep = _with_reports(vals, nr, tr)
        level, worst, lv, r = _score_factors(sv, ls)
    factors = {f: _pack(f, vals.get(f), lv, srcs[f], confs[f]) for f in ("noise", "temp", "humidity")}
    factors["light"] = _light_factor(ls, lv)
    used = [factors[f] for f in ("noise", "temp", "humidity") if factors[f]["source"] != "none"]
    s = [x["source"] for x in used]
    c = [x["confidence"] for x in used if x["confidence"]]
    out = {
        "hour": h,
        "level": level,
        "worst_factors": worst,
        "score": r["score"],
        "grade": r["grade"],
        "source": (s and max(set(s), key=s.count)) or "none",
        "confidence": ("low" if "low" in c else "high") if c else None,
        "factors": factors,
    }
    if used_rep:
        out["reports_used"] = used_rep   # 추정 카페에서 섞은 소음·온도 제보 수
    return out


# ---------------------------------------------------------------------------
# 센서 카페 /indoor
# ---------------------------------------------------------------------------
def _indoor_hours(place_id, d):
    hourly = A._sensor_hourly()[place_id]
    now = A._now_kst()
    cur_slot = (now.date(), now.hour)
    dtype = A._day_type(d)

    base_sum = defaultdict(lambda: defaultdict(list))
    for (hd, hh), v in hourly.items():
        if hd != d and A._day_type(hd) == dtype:
            for f, x in v.items():
                base_sum[hh][f].append(x)

    preds = A._predictions(place_id, d)
    hours = []
    for h in range(24):
        slot = (d, h)
        is_past = slot < cur_slot
        vals, srcs, confs = {}, {}, {}
        for f in ("noise", "temp", "humidity"):
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
            vals[f], srcs[f], confs[f] = value, src, conf
        hours.append(_hour_entry(h, vals, srcs, confs, _light_score(place_id, h)))
    return hours


# ---------------------------------------------------------------------------
# 센서 카페 /now
# ---------------------------------------------------------------------------
_orig_now = A.now


def now(place_id):
    if place_id not in A.PLACES:
        return _orig_now(place_id)          # 데모 카페 등은 예전 그대로
    out = _orig_now(place_id)               # 날씨·생활인구·측정 시각은 기존 계산 재사용
    ind = out["indoor"]
    vals = {f: (ind["factors"].get(f) or {}).get("value") for f in ("noise", "temp", "humidity")}
    ls = _light_score(place_id, A._now_kst().hour)
    level, worst, lv, r = _score_factors(vals, ls)
    factors = {f: {"value": vals[f], "level": lv.get(f, "gray")} for f in ("noise", "temp", "humidity")}
    factors["light"] = {"value": ls, "level": lv.get("light", "gray"),
                        "source": "report" if ls is not None else "none"}
    ind.update(level=level, worst_factors=worst, factors=factors, score=r["score"], grade=r["grade"])
    out["summary_ko"] = A._summary(A.PLACES[place_id]["name"], level, worst, factors, ind["stale"])
    return out


# ---------------------------------------------------------------------------
# 센서 없는 카페 (places_ext): 추정값이 있으면 같은 점수식, 제보만으로는 색을 안 냄
# ---------------------------------------------------------------------------
def _report_counts(place_id, d):
    """추정 카페 소음·온도 제보 개수 {hour: (noise, temp)}. 연결 1번, 1분 캐시. 실패하면 {}."""
    def load():
        try:
            conn = _connect()
        except Exception:
            return {}
        try:
            return {h: (UR.report_counts(conn, place_id, "noise", hour=h, on=d),
                        UR.report_counts(conn, place_id, "temp", hour=h, on=d)) for h in range(24)}
        except Exception:
            return {}
        finally:
            conn.close()
    return A._cache.get(f"reports:{place_id}:{d}", LIGHT_TTL, load)


def _estimated_hours(place_id, d, p):
    near = PX._nearest_sensor(p)
    preds = A._predictions(place_id, d)
    hours = []
    for h in range(24):
        pr = preds.get(h)
        vals, srcs, confs = {}, {}, {}
        for f in ("noise", "temp", "humidity"):
            if pr and pr.get(f) is not None:
                vals[f], srcs[f], confs[f] = float(pr[f]), "estimated", pr.get("confidence") or "low"
            else:
                vals[f], srcs[f], confs[f] = None, "none", None
        has_est = any(v is not None for v in vals.values())
        ls = _light_score(place_id, h) if has_est else None
        nr, tr = (_report_counts(place_id, d).get(h) or (None, None)) if has_est else (None, None)
        e = _hour_entry(h, vals, srcs, confs, ls, nr, tr)
        if has_est:
            e["source"], e["confidence"] = "estimated", e["confidence"] or "low"
        hours.append(e)
    return hours, near


A._indoor_hours = _indoor_hours
A.now = now
PX._estimated_hours = _estimated_hours

# /report, /report/summary 붙이기
A.router.routes.extend(UR.router.routes)
