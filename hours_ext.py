"""
영업시간 밖 gray + 안내 문구 정리 (2026-11-18).

1) 영업시간 밖은 신호등 색을 내지 않음 (level "gray", closed true).
   - /indoor: 그 날짜의 영업시간 밖 시간 칸은 level·factors 색 gray, score/grade null, "closed": true.
     값(value)은 참고용으로 그대로 둠. 영업시간 안 칸은 "closed": false.
   - /now, /places: 지금이 영업시간 밖이면 level gray, "open_now": false.
   - 대상: 센서 카페(cafe-A, cafe-B)와 시연 카페 2곳. 나머지는 원래 gray 라 그대로.
   센서는 밤에도 계속 측정·저장함 (API 응답에서만 숨김).
2) /now 의 summary_ko 에서 "쾌적" 같은 표현을 빼고 "부담이 낮아요/보통이에요/높아요"로 통일.
3) (11/18 저녁) 센서·추정값이 없는 카페도 소음·온도 원탭 제보가 3건 이상이면 제보만으로 색을 냄.
   /places level_source "report", /now data_available true + level_source "report",
   /indoor 그 시간 칸 source "report", "reports_used": {"noise": n, "temp": n}. 제보 집계 규칙은 user_reports 그대로
   (소음: 30일·같은 평일/주말·±1시간, 온도: 7일·±1시간, 한 사람 하루 1건).
4) (11/18 밤) 종합 색을 다시 "소음·온도·습도 항목 색 중 가장 나쁜 색"으로 (sens_ext 의 가중 점수 색을 끔).
   score/grade 는 가중 점수 그대로 응답에 남김(참고·보고서용). 조명(깜빡임)은 색 계산에서 빠짐.
   예민도 쿼리(noise_sens 등)는 받기만 하고 무시함 (개인화는 앱 기기 안에서). 응답 sensitivity 는 null.
6) /places, /now 에 address(도로명, 없으면 지번), dong(예: "정릉동"). places_addr.json(같은 폴더, fill_addr.py 가 만듦)을
   1분마다 다시 읽음. 파일이 없거나 그 장소가 없으면 null.
5) /places 각 장소에 noise_level, temp_level, humidity_level (/now factors 와 같은 항목 색). 앱이 기기 안에서
   민감도로 항목 등급을 올린 뒤 최악색을 다시 고를 수 있게. 영업시간 밖·데이터 없음은 "gray".

설치: app_api.py 맨 끝 `import sens_ext` 아래에 `import hours_ext` 한 줄.
되돌리기: 그 한 줄만 지우고 재시작.
"""

from fastapi import APIRouter

import json
import os

import app_api as A
import demo_ext as DX
import score_ext as SX
import sens_ext as SE
import user_reports as UR

# 영업시간 (KST): (여는 시, 닫는 시)  여는 시 <= hour < 닫는 시 인 칸만 색을 냄
OPEN_HOURS = {
    "cafe-A": (7, 21),   # 포틀리에
    "cafe-B": (10, 21),   # 정릉동커피
}
DEFAULT_HOURS = (9, 22)  # 시연 카페 등 따로 안 적은 곳

BURDEN_KO = {"green": "부담이 낮아요", "yellow": "부담이 보통이에요", "red": "부담이 높아요"}


def _hours_of(place_id):
    if place_id in OPEN_HOURS:
        return OPEN_HOURS[place_id]
    if place_id in A.PLACES or place_id in DX.DEMO:
        return DEFAULT_HOURS
    return None          # 센서·시연값 없는 곳: 원래 gray 라 손대지 않음


def _is_open(place_id, hour):
    hrs = _hours_of(place_id)
    return True if hrs is None else hrs[0] <= hour < hrs[1]


def _hours_text(place_id):
    o, c = _hours_of(place_id)
    return f"{o}시~{c}시"


def _gray_hour(e):
    e["level"], e["worst_factors"], e["score"], e["grade"] = "gray", [], None, None
    for f in (e.get("factors") or {}).values():
        if isinstance(f, dict):
            f["level"] = "gray"
    e["closed"] = True


def _name(place_id, out):
    if place_id in A.PLACES:
        return A.PLACES[place_id]["name"]
    p = DX._demo().get(place_id) or DX.PX._extra_places().get(place_id)
    return p["name"] if p else place_id


# ---------------------------------------------------------------------------
# 제보만으로 색 (센서·시연·추정값 없는 카페)
# ---------------------------------------------------------------------------
REPORT_TTL = 60


def _report_only_place(place_id):
    return place_id not in A.PLACES and place_id not in DX.DEMO


def _reported_places():
    """최근 30일 소음·온도 제보가 있는 장소 id 집합 (1분 캐시, 쿼리 1번)."""
    def load():
        try:
            conn = SX._connect()
        except Exception:
            return set()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT DISTINCT place_id FROM {UR.TABLE} WHERE item IN ('noise','temp') "
                            f"AND created_at >= UTC_TIMESTAMP() - INTERVAL 30 DAY")
                return {r[0] for r in cur.fetchall()}
        except Exception:
            return set()
        finally:
            conn.close()
    return A._cache.get("reported_places", REPORT_TTL, load)


def _counts_now(place_id, d, h):
    """(소음 제보 수 dict, 온도 제보 수 dict) — 그 날짜·시간 기준. 실패하면 (None, None)."""
    def load():
        try:
            conn = SX._connect()
        except Exception:
            return (None, None)
        try:
            return (UR.report_counts(conn, place_id, "noise", hour=h, on=d),
                    UR.report_counts(conn, place_id, "temp", hour=h, on=d))
        except Exception:
            return (None, None)
        finally:
            conn.close()
    return A._cache.get(f"rcount:{place_id}:{d}:{h}", REPORT_TTL, load)


def _report_entry(place_id, h, nr, tr):
    """제보만으로 만든 시간 칸. 제보가 3건 미만이면 None."""
    vals = {"noise": None, "temp": None, "humidity": None}
    sv, used = SX._with_reports(vals, nr, tr)
    if not used:
        return None
    ls = SX._light_score(place_id, h)
    level, worst, lv, r = SX._score_factors(sv, ls)
    if level == "gray":
        return None
    factors = {}
    for f in ("noise", "temp", "humidity"):
        on = f in used
        factors[f] = {"value": None, "level": lv.get(f, "gray") if on else "gray",
                      "source": "report" if on else "none", "confidence": "low" if on else None}
    factors["light"] = SX._light_factor(ls, lv)
    return {"hour": h, "level": level, "worst_factors": worst, "score": None, "grade": None,
            "source": "report", "confidence": "low", "factors": factors, "reports_used": used}


def _report_entry_now(place_id):
    if place_id not in _reported_places():
        return None
    nk = A._now_kst()
    nr, tr = _counts_now(place_id, nk.date(), nk.hour)
    return _report_entry(place_id, nk.hour, nr, tr)


# ---------------------------------------------------------------------------
# 감싸기: sens_ext 가 부르는 원래 처리 함수 앞뒤에 끼움
# ---------------------------------------------------------------------------
_o_places, _o_indoor, _o_now = SE._orig_places, SE._orig_indoor, SE._orig_now


ADDR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "places_addr.json")


def _addr_all():
    def load():
        try:
            with open(ADDR_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return A._cache.get("places_addr", 60, load)


def _put_addr(d, pid):
    a = _addr_all().get(pid) or {}
    d["address"], d["dong"] = a.get("address"), a.get("dong")


ITEM_FIELDS = (("noise", "noise_level"), ("temp", "temp_level"), ("humidity", "humidity_level"))


def _sensor_factors(pid):
    def load():
        try:
            return A.now(pid)["indoor"]["factors"]
        except Exception:
            return {}
    return A._cache.get(f"place_factors:{pid}", 60, load)


def _put_items(it, factors):
    for f, key in ITEM_FIELDS:
        it[key] = ((factors or {}).get(f) or {}).get("level") or "gray"


def places():
    items = _o_places()
    nk = A._now_kst()
    h = nk.hour
    for it in items:
        pid = it["id"]
        _put_addr(it, pid)
        if pid in A.PLACES:
            _put_items(it, _sensor_factors(pid))
        elif pid in DX.DEMO:
            _put_items(it, DX._demo_hour(pid, nk.date(), h)["factors"])
        else:
            _put_items(it, None)
        hrs = _hours_of(it["id"])
        it["open_hours"] = list(hrs) if hrs else None
        it["open_now"] = _is_open(it["id"], h) if hrs else None
        if hrs and not it["open_now"]:
            it["level"] = "gray"
            _put_items(it, None)
        elif _report_only_place(it["id"]) and it.get("level") == "gray":
            e = _report_entry_now(it["id"])
            if e:
                it["level"], it["level_source"] = e["level"], "report"
                _put_items(it, e["factors"])
    return items


def indoor(place_id, date):
    out = _o_indoor(place_id, date)
    if not isinstance(out, dict):
        return out
    if _report_only_place(place_id):
        if place_id in _reported_places():
            d = A._parse_date(date)
            hours = out.get("hours", [])
            for i, e in enumerate(hours):
                if e.get("level") == "gray":
                    nr, tr = _counts_now(place_id, d, e["hour"])
                    re = _report_entry(place_id, e["hour"], nr, tr)
                    if re:
                        hours[i] = re
        return out
    if _hours_of(place_id) is None:
        return out
    out["open_hours"] = list(_hours_of(place_id))
    for e in out.get("hours", []):
        if _is_open(place_id, e["hour"]):
            e["closed"] = False
        else:
            _gray_hour(e)
    return out


def now(place_id):
    out = _o_now(place_id)
    if not isinstance(out, dict):
        return out
    _put_addr(out, place_id)
    ind = out.get("indoor") or {}
    topic = DX._topic(_name(place_id, out))
    hrs = _hours_of(place_id)
    if hrs:
        out["open_hours"] = list(hrs)
        out["open_now"] = _is_open(place_id, A._now_kst().hour)
        if not out["open_now"]:
            ind["level"], ind["worst_factors"], ind["score"], ind["grade"] = "gray", [], None, None
            for f in (ind.get("factors") or {}).values():
                if isinstance(f, dict):
                    f["level"] = "gray"
            out["summary_ko"] = f"{topic} 지금 영업시간이 아니에요 (영업 {_hours_text(place_id)})."
            return out
    else:
        out["open_hours"], out["open_now"] = None, None
        if _report_only_place(place_id) and ind.get("level") == "gray" and out.get("data_available") is False:
            e = _report_entry_now(place_id)
            if e:
                ind.update(level=e["level"], worst_factors=e["worst_factors"], score=e["score"],
                           grade=e["grade"], source="report", confidence="low", stale=False,
                           factors={f: {"value": v["value"], "level": v["level"]} for f, v in e["factors"].items()},
                           reports_used=e["reports_used"])
                out["data_available"], out["level_source"] = True, "report"
    level = ind.get("level")
    if level in BURDEN_KO:
        names = ", ".join(A.FACTOR_KO.get(f, f) for f in ind.get("worst_factors") or [])
        s = f"{topic} 지금 {names} 때문에 {BURDEN_KO[level]}." if names and level != "green" \
            else f"{topic} 지금 {BURDEN_KO[level]}."
        if out.get("is_demo"):
            s += " (시연용 가정값)"
        elif out.get("level_source") == "report":
            s = s.replace(" 지금 ", " 이용자 제보로는 지금 ", 1)
        out["summary_ko"] = s
    return out


SE._orig_places, SE._orig_indoor, SE._orig_now = places, indoor, now


# ---------------------------------------------------------------------------
# 4) 종합 색 = 소음·온도·습도 중 가장 나쁜 색, 예민도 무시
# ---------------------------------------------------------------------------
LEVEL_FACTORS = ("noise", "temp", "humidity")
LEVELS = ("green", "yellow", "red")


def _score_factors(vals, ls):
    SS = SX.SS
    r = SS.sensory_score(vals.get("noise"), vals.get("temp"), vals.get("humidity"), light_score=ls)
    lv = {}
    if vals.get("noise") is not None:
        lv["noise"] = SX._color_of_item(SS._one_sided(vals["noise"], SS.NOISE_BEST, SS.NOISE_WORST))
    if vals.get("temp") is not None:
        lv["temp"] = SX._color_of_item(SS._two_sided(vals["temp"], SS.TEMP_OK, SS.TEMP_ZERO))
    if vals.get("humidity") is not None:
        lv["humidity"] = SX._color_of_item(SS._two_sided(vals["humidity"], SS.HUM_OK, SS.HUM_ZERO))
    if ls is not None:
        lv["light"] = SX._color_of_item(ls)          # 표시용, 색 계산엔 안 씀
    known = [lv[f] for f in LEVEL_FACTORS if f in lv]
    if not known:
        return "gray", [], lv, r
    rank = max(A.LEVEL_RANK[c] for c in known)
    worst = [f for f in LEVEL_FACTORS if lv.get(f) and A.LEVEL_RANK[lv[f]] == rank] if rank > 0 else []
    return LEVELS[rank], worst, lv, r


SX._score_factors = _score_factors
DX._score_factors = _score_factors
SE._sens_dict = lambda *a: None          # 예민도 쿼리 무시

# 시연 카페 소음 범위를 이 규칙으로 다시 계산
DX._bands.clear()
for _c in LEVELS:
    DX._band(_c)


_th = APIRouter()


@_th.get("/thresholds")
def _thresholds():
    SS = SX.SS
    return {
        "rule": "종합 level = 소음·온도·습도 항목 색 중 가장 나쁜 색 (조명·깜빡임은 색 계산에서 제외, 값 없는 항목은 무시)",
        "item_color": "항목 점수(0~10) 7.5 이상 green, 5.5 이상 yellow, 그 아래 red",
        "noise_db": {"score_10_at_or_below": SS.NOISE_BEST, "score_0_at_or_above": SS.NOISE_WORST},
        "temp_c": {"score_10_range": list(SS.TEMP_OK), "score_0_at": list(SS.TEMP_ZERO)},
        "humidity_pct": {"score_10_range": list(SS.HUM_OK), "score_0_at": list(SS.HUM_ZERO)},
        "score": "score/grade 는 가중 점수(깜빡임 제보 3건 이상이면 조명 포함), 참고용. 색에는 안 씀",
        "personalize": "서버는 예민도를 쓰지 않음. 앱이 기기 안에서 등급을 올림",
    }


A.router.routes[:0] = _th.routes
