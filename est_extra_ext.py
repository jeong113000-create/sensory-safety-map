"""
센서 없는 '추정 카페' 추가 (2026-11-24). 설정 파일 /home/ubuntu/est_extra.json 을 읽음.
  {"kakao-…": {"name": …, "src": "cafe-A", "open": [12, 18], "closed_days": [6]}}

- 그 카페를 추정 카페로 보여 줌(level_source "estimated"): final_ext 의 ALLOW 에 추가.
- 예측값: predictions 표에 그 카페 행이 있으면 그걸 쓰고, 없으면 src 센서 카페(포틀리에)의 예측을 그대로 빌려 씀
  (confidence "low"). 학습 서버가 500 m 안 카페에 하는 복사와 같은 방식을 서버에서 함.
- 영업시간·휴무: hours2_ext 와 같은 형식(open_hours, closed_days, 휴무일·시간 밖 회색).

설치: app_api.py 맨 끝(final_ext, hours2_ext 보다 아래)에 `import est_extra_ext` 한 줄.
"""
import json
import os

import app_api as A
import est_ext as EX
import final_ext as FX
import hours2_ext as H2

CONF = os.environ.get("EST_EXTRA_CONF", "/home/ubuntu/est_extra.json")
try:
    with open(CONF, encoding="utf-8") as f:
        EXTRA = json.load(f)
except Exception:
    EXTRA = {}

for pid, c in EXTRA.items():
    FX.ALLOW.add(pid)
    o, cl = c.get("open") or (9, 22)
    H2.HOURS2[pid] = (int(o), int(cl), set(c.get("closed_days") or []))

_prev_predictions = A._predictions


def _predictions(place_id, d):
    rows = _prev_predictions(place_id, d)
    if rows or place_id not in EXTRA:
        return rows
    src = _prev_predictions(EXTRA[place_id].get("src", "cafe-A"), d)
    return {h: dict(r, confidence="low") for h, r in src.items()}


A._predictions = _predictions
_prev_preds_now = EX._preds_now


def _preds_now():
    out = dict(_prev_preds_now())
    if not EXTRA:
        return out
    nk = A._now_kst()
    for pid in EXTRA:
        if pid in out:
            continue
        r = A._cache.get(f"est_extra:{pid}:{nk.date()}", 60, lambda pid=pid: _predictions(pid, nk.date())).get(nk.hour)
        if r:
            out[pid] = {"place_id": pid, "noise": r.get("noise"), "temp": r.get("temp"),
                        "humidity": r.get("humidity"), "confidence": "low"}
    return out


EX._preds_now = _preds_now
