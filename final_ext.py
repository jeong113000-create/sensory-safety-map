"""
최종 시연 구성 (2026-11-21).

- 카페쿠피(kakao-92860343): 정릉동커피 목 온습도로 학습한 예측 → level_source "demo", is_demo true,
  summary "(시연용 가정값)".
- 차담 by 갤러리더스퀘어(kakao-1795099660): 포틀리에 실측으로 학습한 예측 → level_source "estimated".
- 나머지 센서 없는 카페: 예측이 DB에 있어도 쓰지 않음 → 원탭 제보 3건↑면 "report", 아니면 gray "none".
- 예전 시간대 패턴 시연값(demo_ext)은 끔. 두 카페 모두 predictions 테이블 값으로 나옴.
- est_fix_ext 의 "가장 가까운 센서가 정릉동커피면 습도 빼기" 규칙은 끔(카페쿠피 목 습도는 써야 함).
  25% 미만 습도 빼기는 그대로.
- (11/21 새벽 추가) 정릉동커피(cafe-B)의 시간대 예측은 학습 서버 그룹 B(소음 실측 + 목 온습도)라서
  /indoor 의 예측 칸(source "model")을 source "demo"로 바꾸고 "forecast_is_demo": true 를 붙임.
  지난 시간 칸(measured)·/now·/places 의 지금 색은 실시간 센서 그대로. 습도 색 제외(humoff_ext)도 그대로.

설치: app_api.py 맨 끝 `import est_fix_ext` 아래에 `import final_ext` 한 줄.
"""

import app_api as A
import demo_ext as DX
import est_ext as EX
import est_fix_ext as EF
import sens_ext as SE

MOCK = {"kakao-92860343"}                       # 목데이터 모델 → "demo"
ALLOW = MOCK | {"kakao-1795099660"}             # 예측을 보여줄 센서 없는 카페

# 1) 패턴 시연값 끄기
DX.DEMO.clear()
DX._bands.clear()

# 2) 가장 가까운 센서 기준 습도 빼기 끄기
EF._near_off = lambda pid: False

# 3) 예측은 ALLOW 카페만
_prev_predictions = A._predictions


def _predictions(place_id, d):
    if place_id not in A.PLACES and place_id not in ALLOW:
        return {}
    return _prev_predictions(place_id, d)


A._predictions = _predictions
_prev_preds_now = EX._preds_now


def _preds_now():
    return {pid: r for pid, r in _prev_preds_now().items() if pid in ALLOW}


EX._preds_now = _preds_now


# 4) 목데이터 카페는 "demo" 로 표시
def _relabel(obj):
    if isinstance(obj, dict):
        if obj.get("source") == "estimated":
            obj["source"] = "demo"
        for v in obj.values():
            _relabel(v)
    elif isinstance(obj, list):
        for v in obj:
            _relabel(v)


_prev_places = SE._orig_places


def places():
    items = _prev_places()
    for it in items:
        if it.get("id") in MOCK:
            it["is_demo"] = True
            if it.get("level_source") == "estimated":
                it["level_source"] = "demo"
    return items


_prev_now = SE._orig_now


def now(place_id):
    out = _prev_now(place_id)
    if place_id in MOCK and isinstance(out, dict):
        out["is_demo"] = True
        if out.get("level_source") == "estimated":
            out["level_source"] = "demo"
            _relabel(out.get("indoor"))
            s = out.get("summary_ko")
            if isinstance(s, str):
                out["summary_ko"] = s.replace("추정으로는 ", "").replace("(추정값)", "(시연용 가정값)")
    return out


_prev_indoor = SE._orig_indoor


def indoor(place_id, date):
    out = _prev_indoor(place_id, date)
    if place_id in MOCK and isinstance(out, dict):
        out["is_demo"] = True
        if out.get("level_source") == "estimated":
            out["level_source"] = "demo"
        _relabel(out.get("hours"))
    return out


SE._orig_places, SE._orig_now, SE._orig_indoor = places, now, indoor


# 5) 정릉동커피: 예측 칸은 시연용 (지난 칸·지금 색은 실측 그대로)
DEMO_FORECAST = {"cafe-B"}
_prev_indoor2 = SE._orig_indoor


def _demo_forecast(e):
    if e.get("source") == "model":
        e["source"], e["confidence"] = "demo", "low"
    for f in (e.get("factors") or {}).values():
        if isinstance(f, dict) and f.get("source") == "model":
            f["source"], f["confidence"] = "demo", "low"


def indoor2(place_id, date):
    out = _prev_indoor2(place_id, date)
    if place_id in DEMO_FORECAST and isinstance(out, dict):
        for e in out.get("hours") or []:
            _demo_forecast(e)
        out["forecast_is_demo"] = True
    return out


SE._orig_indoor = indoor2
