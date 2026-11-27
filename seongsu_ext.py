"""
발표장(성수 상상플래닛) 1층 카페 '위스탠커피 성수' (2026-11-21, 발표 11/28 토 14:30).

- places 테이블에 id "seongsu-wistan" 으로 넣은 카페(센서·예측 없음, 원탭 제보 3건↑면 색).
- 영업시간: 평일 9~20시, 주말 11~20시. open_hours 는 그날 요일 기준 한 쌍, open_hours_week 에 둘 다.
  영업시간 밖이면 gray, closed true. /indoor 는 그 날짜 요일 기준으로 시간별 gray.
- 정릉 센서 카페와 8 km 떨어져 있어서 "가장 가까운 정릉동커피 참고" 문구 대신 제보 안내 문구.

설치: app_api.py 맨 끝에 `import seongsu_ext` 한 줄 (hours_ext 보다 아래면 어디든).
"""

import app_api as A
import hours_ext as HX
import sens_ext as SE

SID = "seongsu-wistan"
WEEK = {"weekday": (9, 20), "weekend": (11, 20)}


def _hours(d):
    return WEEK["weekend" if A._day_type(d) == "weekend" else "weekday"]


def _put_hours(o, d, hour=None):
    o_, c_ = _hours(d)
    o["open_hours"] = [o_, c_]
    o["open_hours_week"] = {k: list(v) for k, v in WEEK.items()}
    if hour is not None:
        o["open_now"] = o_ <= hour < c_
    return o_, c_


_prev_places = SE._orig_places


def places():
    items = _prev_places()
    nk = A._now_kst()
    for it in items:
        if it.get("id") != SID:
            continue
        _put_hours(it, nk.date(), nk.hour)
        if not it["open_now"]:
            it["level"], it["level_source"] = "gray", "none"
            HX._put_items(it, None)
    return items


_prev_now = SE._orig_now


def now(place_id):
    out = _prev_now(place_id)
    if place_id != SID or not isinstance(out, dict):
        return out
    nk = A._now_kst()
    o_, c_ = _put_hours(out, nk.date(), nk.hour)
    ind = out.get("indoor") or {}
    if not out["open_now"]:
        ind.update(level="gray", worst_factors=[], score=None, grade=None)
        for f in (ind.get("factors") or {}).values():
            if isinstance(f, dict):
                f["level"] = "gray"
        out["data_available"], out["level_source"] = False, "none"
        out["summary_ko"] = f"위스탠커피 성수는 지금 영업시간이 아니에요 (오늘 영업 {o_}시~{c_}시)."
    elif out.get("level_source") != "report":
        out["level_source"] = out.get("level_source") or "none"
        out["summary_ko"] = "위스탠커피 성수는 아직 센서가 없어서 상태를 알 수 없어요. 이용자 제보가 모이면 색으로 알려 드려요."
    return out


_prev_indoor = SE._orig_indoor


def indoor(place_id, date):
    out = _prev_indoor(place_id, date)
    if place_id != SID or not isinstance(out, dict):
        return out
    o_, c_ = _put_hours(out, A._parse_date(date))
    for e in out.get("hours", []):
        if isinstance(e, dict):
            if o_ <= e.get("hour", -1) < c_:
                e["closed"] = False
            else:
                HX._gray_hour(e)
    return out


SE._orig_places, SE._orig_now, SE._orig_indoor = places, now, indoor
