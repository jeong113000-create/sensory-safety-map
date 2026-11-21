"""
소음 항목 색 경계를 Rindel 값으로 (2026-11-18 밤).

- 소음 항목 색: 71 dB 이하 초록, 71~80 dB 노랑, 80 dB 이상 빨강 (sensory_score.item_level).
- 온도·습도 항목 색은 그대로 (항목 점수 7.5 이상 초록, 5.5 이상 노랑).
- 종합 색 = 소음·온도·습도 항목 색 중 가장 나쁜 색 (hours_ext 규칙 그대로, 조명 제외).
- score/grade(가중 점수, 참고용)·제보·예민도 무시는 바뀌지 않음.
- 시연 카페 소음 값도 새 경계에 맞게 다시 계산.

설치: app_api.py 맨 끝 `import hours_ext` 아래에 `import rindel_ext` 한 줄.
되돌리기: 그 한 줄만 지우고 재시작.
"""

from fastapi import APIRouter

import app_api as A
import demo_ext as DX
import hours_ext as HX
import score_ext as SX

SS = SX.SS


def _score_factors(vals, ls):
    r = SS.sensory_score(vals.get("noise"), vals.get("temp"), vals.get("humidity"), light_score=ls)
    lv = {}
    if vals.get("noise") is not None:
        lv["noise"] = SS.item_level("noise", SS._one_sided(vals["noise"], SS.NOISE_BEST, SS.NOISE_WORST))
    if vals.get("temp") is not None:
        lv["temp"] = SS.item_level("temp", SS._two_sided(vals["temp"], SS.TEMP_OK, SS.TEMP_ZERO))
    if vals.get("humidity") is not None:
        lv["humidity"] = SS.item_level("humidity", SS._two_sided(vals["humidity"], SS.HUM_OK, SS.HUM_ZERO))
    if ls is not None:
        lv["light"] = SX._color_of_item(ls)          # 표시용, 색 계산엔 안 씀
    known = [lv[f] for f in HX.LEVEL_FACTORS if f in lv]
    if not known:
        return "gray", [], lv, r
    rank = max(A.LEVEL_RANK[c] for c in known)
    worst = [f for f in HX.LEVEL_FACTORS if lv.get(f) and A.LEVEL_RANK[lv[f]] == rank] if rank > 0 else []
    return HX.LEVELS[rank], worst, lv, r


SX._score_factors = _score_factors
DX._score_factors = _score_factors
HX._score_factors = _score_factors


# 시연 카페: 종합 색과 소음 항목 색이 둘 다 그 색이 되는 소음 범위
def _band(color):
    if color not in DX._bands:
        xs = [x / 10 for x in range(400, 951)]
        ok = [x for x in xs
              if _score_factors({"noise": x, "temp": DX.DEMO_TEMP, "humidity": DX.DEMO_HUM}, DX.DEMO_LIGHT)[0] == color
              and SS.item_level("noise", SS._one_sided(x, SS.NOISE_BEST, SS.NOISE_WORST)) == color]
        DX._bands[color] = (min(ok), max(ok)) if ok else (None, None)
    return DX._bands[color]


DX._band = _band
DX._bands.clear()
for _c in HX.LEVELS:
    _band(_c)


_th = APIRouter()


@_th.get("/thresholds")
def _thresholds():
    return {
        "rule": "종합 level = 소음·온도·습도 항목 색 중 가장 나쁜 색 (조명·깜빡임은 색 계산에서 제외, 값 없는 항목은 무시)",
        "item_color": {
            "noise": f"{SS.NOISE_GREEN_MAX_DB:g} dB 이하 green, {SS.NOISE_RED_MIN_DB:g} dB 이상 red, 그 사이 yellow (Rindel)",
            "temp": "항목 점수(0~10) 7.5 이상 green, 5.5 이상 yellow, 그 아래 red",
            "humidity": "항목 점수(0~10) 7.5 이상 green, 5.5 이상 yellow, 그 아래 red",
        },
        "noise_color_db": {"green_at_or_below": SS.NOISE_GREEN_MAX_DB, "red_at_or_above": SS.NOISE_RED_MIN_DB},
        "noise_db": {"score_10_at_or_below": SS.NOISE_BEST, "score_0_at_or_above": SS.NOISE_WORST},
        "temp_c": {"score_10_range": list(SS.TEMP_OK), "score_0_at": list(SS.TEMP_ZERO)},
        "humidity_pct": {"score_10_range": list(SS.HUM_OK), "score_0_at": list(SS.HUM_ZERO)},
        "score": "score/grade 는 가중 점수(깜빡임 제보 3건 이상이면 조명 포함), 참고용. 색에는 안 씀",
        "personalize": "서버는 예민도를 쓰지 않음. 앱이 기기 안에서 등급을 올림",
    }


A.router.routes[:0] = _th.routes
