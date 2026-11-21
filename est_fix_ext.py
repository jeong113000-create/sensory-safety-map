"""
추정 카페 손보기 (2026-11-19).

1) 추정 습도 빼기: 센서 없는 카페의 예측 습도가
   - 가장 가까운 센서 카페가 습도 고장 카페(humoff_ext.HUM_OFF, 지금 cafe-B)이거나
   - 값이 25% 미만(실내에서 거의 불가능한 값)이면
   그 습도는 값·색 모두 빼고(gray) "excluded_factors": ["humidity"] 를 붙임. /places, /indoor, /now 모두 같음.
2) /now summary_ko 끝 "(예측값)" → "(추정값)".
3) /indoor 맨 위에 level_source 추가: sensor / demo / estimated / report / none.

설치: app_api.py 맨 끝 `import humoff_ext` 아래에 `import est_fix_ext` 한 줄.
"""

import app_api as A
import demo_ext as DX
import est_ext as EX
import humoff_ext as HO
import places_ext as PX
import sens_ext as SE

HUM_MIN = 25.0


def _est_place(pid):
    return pid not in A.PLACES and pid not in DX.DEMO


def _near_off(pid):
    p = PX._extra_places().get(pid)
    if not p:
        return False
    near = PX._nearest_sensor(p)
    return bool(near and near["id"] in HO.HUM_OFF)


def _drop_hum(pid, row):
    """row 의 humidity 를 빼야 하면 습도만 None 인 복사본, 아니면 그대로."""
    if not row or row.get("humidity") is None or not _est_place(pid):
        return row
    if _near_off(pid) or float(row["humidity"]) < HUM_MIN:
        return dict(row, humidity=None)
    return row


def _hum_dropped(pid):
    """오늘 예측에서 이 카페 습도를 뺐는지."""
    if not _est_place(pid):
        return False
    if _near_off(pid):
        return True
    raw = _prev_predictions(pid, A._now_kst().date())
    return any(r.get("humidity") is not None and float(r["humidity"]) < HUM_MIN for r in raw.values())


# /indoor·/now 추정(places_ext._estimated_hours)이 쓰는 예측
_prev_predictions = A._predictions


def _predictions(place_id, d):
    rows = _prev_predictions(place_id, d)
    if not _est_place(place_id):
        return rows
    return {h: _drop_hum(place_id, r) for h, r in rows.items()}


A._predictions = _predictions

# /places 추정이 쓰는 지금 시각 예측
_prev_preds_now = EX._preds_now


def _preds_now():
    return {pid: _drop_hum(pid, r) for pid, r in _prev_preds_now().items()}


EX._preds_now = _preds_now

# /places: 습도 뺀 카페 표시
_prev_places = SE._orig_places


def places():
    items = _prev_places()
    raw = _prev_preds_now()
    for it in items:
        pid = it.get("id")
        if it.get("level_source") == "estimated" and raw.get(pid) is not None \
                and _drop_hum(pid, raw[pid]) is not raw[pid]:
            it["excluded_factors"] = ["humidity"]
    return items


# /now: (예측값) → (추정값), 습도 뺀 표시
_prev_now = SE._orig_now


def now(place_id):
    out = _prev_now(place_id)
    if not isinstance(out, dict):
        return out
    s = out.get("summary_ko")
    if isinstance(s, str):
        out["summary_ko"] = s.replace("(예측값)", "(추정값)")
    if out.get("level_source") == "estimated" and _hum_dropped(place_id):
        out["excluded_factors"] = ["humidity"]
    return out


# /indoor: level_source, 습도 뺀 표시
_prev_indoor = SE._orig_indoor


def _indoor_source(place_id, out):
    if place_id in A.PLACES:
        return "sensor"
    if place_id in DX.DEMO:
        return "demo"
    srcs = {e.get("source") for e in out.get("hours", []) if isinstance(e, dict)}
    for s in ("estimated", "report"):
        if s in srcs:
            return s
    return "none"


def indoor(place_id, date):
    out = _prev_indoor(place_id, date)
    if not isinstance(out, dict):
        return out
    out["level_source"] = _indoor_source(place_id, out)
    if out["level_source"] == "estimated" and _est_place(place_id):
        d = A._parse_date(date)
        raw = _prev_predictions(place_id, d)
        if any(_drop_hum(place_id, r) is not r for r in raw.values()):
            out["excluded_factors"] = ["humidity"]
    return out


SE._orig_places, SE._orig_now, SE._orig_indoor = places, now, indoor
