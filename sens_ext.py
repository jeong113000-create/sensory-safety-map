"""
신호등 색을 가중 점수로 되돌리고, 온보딩 예민도 개인화를 반영하는 연결 파일 (2026-11-18).

- 종합 색 = sensory_score의 가중 점수 색 (보고서 5.5 ②). 11/15 demo_ext의 "가장 나쁜 항목 색" 규칙을 끔.
  깜빡임 제보가 최근 30일 3건 이상이면 조명 항목도 점수에 들어감 (소음 0.49·조명 0.36·온습도 0.15),
  부족하면 소음 0.76·온습도 0.24. 안전 규칙(가장 나쁜 항목보다 2단계 넘게 좋을 수 없음)은 그대로.
- 개인화: /places, /places/{id}/indoor, /places/{id}/now 에 쿼리 파라미터 3개(선택, 1~5, 보통 3)
      noise_sens (소리)  light_sens (빛·깜빡임)  temp_sens (온도)
  를 붙이면 그 사람 예민도로 점수·색을 계산함. 안 붙이면 예전과 같은 결과(논문 가중치).
  예민도는 저장하지 않고 그 요청 계산에만 씀.
- 시연 카페 2곳의 소음 값도 가중 점수 기준(예민도 보통)으로 다시 맞춤. 예민도를 붙이면 시연 카페 색도 달라짐.

설치: app_api.py 맨 끝 `import demo_ext` 아래에 `import sens_ext` 한 줄.
되돌리기: 그 한 줄만 지우고 재시작.
"""

import contextvars
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.routing import APIRoute

import app_api as A
import demo_ext as DX
import score_ext as SX
import sensory_score as SS

# ---------------------------------------------------------------------------
# 요청마다 예민도 (스레드·요청 간에 섞이지 않게 contextvar)
# ---------------------------------------------------------------------------
SENS = contextvars.ContextVar("sensory_sensitivity", default=None)


def _sens_dict(noise_sens, light_sens, temp_sens):
    d = {k: v for k, v in (("noise", noise_sens), ("light", light_sens), ("thermal", temp_sens)) if v is not None}
    return d or None


_orig_sensory_score = SS.sensory_score


def sensory_score(*args, sensitivity=None, **kw):
    if sensitivity is None:
        sensitivity = SENS.get()
    return _orig_sensory_score(*args, sensitivity=sensitivity, **kw)


SS.sensory_score = sensory_score

# /places 의 장소별 색 캐시는 예민도마다 따로 (같은 1분 캐시를 다른 사람 예민도로 돌려주지 않게)
_orig_cache_get = A._cache.get


def _cache_get(key, ttl, load):
    s = SENS.get()
    if s and str(key).startswith("place_level:"):
        key = f"{key}|{sorted(s.items())}"
    return _orig_cache_get(key, ttl, load)


A._cache.get = _cache_get


# ---------------------------------------------------------------------------
# 종합 색: 가중 점수 (score_ext 원래 계산. demo_ext가 바꿔 끼운 것을 되돌림)
# ---------------------------------------------------------------------------
def _score_factors(vals, ls):
    r = SS.sensory_score(vals.get("noise"), vals.get("temp"), vals.get("humidity"), light_score=ls)
    lv = {}
    if vals.get("noise") is not None:
        lv["noise"] = SX._color_of_item(SS._one_sided(vals["noise"], SS.NOISE_BEST, SS.NOISE_WORST))
    if vals.get("temp") is not None:
        lv["temp"] = SX._color_of_item(SS._two_sided(vals["temp"], SS.TEMP_OK, SS.TEMP_ZERO))
    if vals.get("humidity") is not None:
        lv["humidity"] = SX._color_of_item(SS._two_sided(vals["humidity"], SS.HUM_OK, SS.HUM_ZERO))
    if ls is not None:
        lv["light"] = SX._color_of_item(ls)
    level = r["color"]
    worst = []
    if level not in ("gray", "green") and lv:
        rank = max(A.LEVEL_RANK[c] for c in lv.values())
        if rank > 0:
            worst = [f for f in A.FACTORS if lv.get(f) and A.LEVEL_RANK[lv[f]] == rank]
    return level, worst, lv, r


SX._score_factors = _score_factors
DX._score_factors = _score_factors
A.FACTOR_KO["light"] = "조명"

# 시연 카페 소음 범위를 가중 점수 기준(예민도 보통)으로 다시 계산
DX._bands.clear()
for _c in ("green", "yellow", "red"):
    DX._band(_c)


# ---------------------------------------------------------------------------
# 경로: 예민도 파라미터를 받아 원래 처리 함수를 그대로 부름
# ---------------------------------------------------------------------------
def _route(path):
    for r in A.router.routes:
        if isinstance(r, APIRoute) and r.path == path and "GET" in r.methods:
            return r.endpoint
    raise RuntimeError(f"route not found: {path}")


_orig_places = _route("/places")
_orig_indoor = _route("/places/{place_id}/indoor")
_orig_now = _route("/places/{place_id}/now")

_ext = APIRouter()
_Q = dict(default=None, ge=1, le=5)


def _run(sens, fn, *args):
    tok = SENS.set(sens)
    try:
        out = fn(*args)
    finally:
        SENS.reset(tok)
    if isinstance(out, dict):
        out["sensitivity"] = sens
    return out


@_ext.get("/places")
def list_places(noise_sens: Optional[float] = Query(**_Q), light_sens: Optional[float] = Query(**_Q),
                temp_sens: Optional[float] = Query(**_Q)):
    return _run(_sens_dict(noise_sens, light_sens, temp_sens), _orig_places)


@_ext.get("/places/{place_id}/indoor")
def indoor(place_id: str, date: str = Query(None, description="YYYY-MM-DD (KST), 생략 시 오늘"),
           noise_sens: Optional[float] = Query(**_Q), light_sens: Optional[float] = Query(**_Q),
           temp_sens: Optional[float] = Query(**_Q)):
    return _run(_sens_dict(noise_sens, light_sens, temp_sens), _orig_indoor, place_id, date)


@_ext.get("/places/{place_id}/now")
def now(place_id: str, noise_sens: Optional[float] = Query(**_Q), light_sens: Optional[float] = Query(**_Q),
        temp_sens: Optional[float] = Query(**_Q)):
    return _run(_sens_dict(noise_sens, light_sens, temp_sens), _orig_now, place_id)


@_ext.get("/thresholds")
def thresholds():
    return {
        "rule": "종합 level = 가중 점수(0~100) 등급 색. 85↑·75↑ green, 65↑·55↑ yellow, 그 아래 red. "
                "전체 등급은 가장 나쁜 항목 등급보다 2단계 넘게 좋을 수 없음(안전 규칙)",
        "weights": {"with_flicker_reports": SS.weights(True), "without": SS.weights(False)},
        "item_color": "항목 점수(0~10) 7.5 이상 green, 5.5 이상 yellow, 그 아래 red (항목별 색 표시용)",
        "noise_db": {"score_10_at_or_below": SS.NOISE_BEST, "score_0_at_or_above": SS.NOISE_WORST},
        "temp_c": {"score_10_range": list(SS.TEMP_OK), "score_0_at": list(SS.TEMP_ZERO)},
        "humidity_pct": {"score_10_range": list(SS.HUM_OK), "score_0_at": list(SS.HUM_ZERO)},
        "light": "깜빡임 원탭 제보 조명점수(0~10). 최근 30일 3건 이상일 때만 점수에 들어감",
        "personalize": "noise_sens·light_sens·temp_sens (1~5, 보통 3) 쿼리로 예민도 반영. "
                       "감각부담 = Σ 가중치 × (예민도÷3) × (10 − 항목점수), 점수 = 100 − 10 × 감각부담",
    }


# demo_ext·places_ext·app_api 의 같은 경로보다 먼저 매칭되도록 맨 앞에
A.router.routes[:0] = _ext.routes
