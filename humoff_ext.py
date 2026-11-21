"""
카페 B 습도를 종합 색에서 잠시 뺌 (2026-11-18 밤, 습도 센서 점검 전까지).

- cafe-B 의 습도는 값은 그대로 보여주되 색 계산(종합 색·점수)에서 뺌. 습도 항목 색은 "gray".
- /now, /indoor 응답에 "excluded_factors": ["humidity"] 표시.
- 다른 카페·다른 항목은 그대로.

설치: app_api.py 맨 끝 `import est_ext` 아래에 `import humoff_ext` 한 줄.
되돌리기(센서 고친 뒤): 그 한 줄만 지우고 재시작.
"""

import contextvars

import app_api as A
import demo_ext as DX
import hours_ext as HX
import score_ext as SX

HUM_OFF = {"cafe-B"}

_PLACE = contextvars.ContextVar("humoff_place", default=None)
_prev_sf = SX._score_factors


def _score_factors(vals, ls):
    if _PLACE.get() in HUM_OFF and vals.get("humidity") is not None:
        vals = dict(vals, humidity=None)
    return _prev_sf(vals, ls)


SX._score_factors = _score_factors
DX._score_factors = _score_factors
HX._score_factors = _score_factors

_prev_indoor_hours = A._indoor_hours
_prev_now = A.now


def _indoor_hours(place_id, d):
    tok = _PLACE.set(place_id)
    try:
        return _prev_indoor_hours(place_id, d)
    finally:
        _PLACE.reset(tok)


def now(place_id):
    tok = _PLACE.set(place_id)
    try:
        out = _prev_now(place_id)
    finally:
        _PLACE.reset(tok)
    if place_id in HUM_OFF and isinstance(out, dict):
        out["excluded_factors"] = ["humidity"]
    return out


A._indoor_hours = _indoor_hours
A.now = now

# /indoor 응답에도 표시
_prev_indoor_route = HX.SE._orig_indoor


def _indoor(place_id, date):
    out = _prev_indoor_route(place_id, date)
    if place_id in HUM_OFF and isinstance(out, dict):
        out["excluded_factors"] = ["humidity"]
    return out


HX.SE._orig_indoor = _indoor
