"""
센서 없는 카페(kakao-…)에 학습 서버 예측(predictions)이 있으면 /places·/now 도 "추정" 색으로 (2026-11-18 밤).

지금까지: /indoor 는 예측이 있으면 추정 색이 나왔지만, /places 와 /now 는 항상 gray 였음.
바꾼 뒤:
- /places: 지금 시각(KST) 예측이 있는 카페는 level = 추정 색, level_source "estimated",
  noise_level·temp_level·humidity_level 도 추정 항목 색. 예측이 없으면 예전 그대로(gray, 또는 제보 3건↑면 "report").
- /now: 지금 시각 예측이 있으면 data_available true, level_source "estimated",
  indoor.source "estimated", confidence "low", summary_ko "○○는 추정으로는 지금 … (예측값)".
- 색 규칙은 센서 카페와 같음 (소음 71/80 dB Rindel, 온습도 7.5/5.5, 최악색). 소음·온도 제보가 있으면 /indoor 와 같이 섞음.
- 센서 카페(cafe-A/B)·시연 카페는 그대로.

설치: app_api.py 맨 끝 `import rindel_ext` 아래에 `import est_ext` 한 줄.
되돌리기: 그 한 줄만 지우고 재시작.
"""

import app_api as A
import demo_ext as DX
import hours_ext as HX
import places_ext as PX
import score_ext as SX
import sens_ext as SE
import user_reports as UR

TTL = 60
ITEMS = ("noise", "temp", "humidity")


def _est_place(pid):
    return pid not in A.PLACES and pid not in DX.DEMO


def _preds_now():
    """지금 시각 예측 {place_id: row}. 쿼리 1번, 1분 캐시."""
    nk = A._now_kst()
    d, h = nk.date(), nk.hour

    def load():
        try:
            rows = A._q("SELECT place_id, noise, temp, humidity, confidence FROM predictions "
                        "WHERE date=%s AND hour=%s", (d, h))
        except Exception:
            return {}
        return {r["place_id"]: r for r in rows}
    return A._cache.get(f"est_preds:{d}:{h}", TTL, load)


def _counts_now(pids):
    """제보가 있는 추정 카페만 지금 시각 소음·온도 제보 수 {pid: (noise, temp)}. 연결 1번, 1분 캐시."""
    nk = A._now_kst()
    d, h = nk.date(), nk.hour
    pids = sorted(set(pids) & set(HX._reported_places()))

    def load():
        if not pids:
            return {}
        try:
            conn = SX._connect()
        except Exception:
            return {}
        try:
            return {p: (UR.report_counts(conn, p, "noise", hour=h, on=d),
                        UR.report_counts(conn, p, "temp", hour=h, on=d)) for p in pids}
        except Exception:
            return {}
        finally:
            conn.close()
    return A._cache.get(f"est_counts:{d}:{h}:{','.join(pids)}", TTL, load)


def _entry_from_row(h, row, counts):
    vals, srcs, confs = {}, {}, {}
    for f in ITEMS:
        v = row.get(f)
        if v is not None:
            vals[f], srcs[f], confs[f] = float(v), "estimated", row.get("confidence") or "low"
        else:
            vals[f], srcs[f], confs[f] = None, "none", None
    if all(v is None for v in vals.values()):
        return None
    nr, tr = counts or (None, None)
    e = SX._hour_entry(h, vals, srcs, confs, None, nr, tr)
    e["source"], e["confidence"] = "estimated", e["confidence"] or "low"
    return e


# ---------------------------------------------------------------------------
# /places
# ---------------------------------------------------------------------------
_h_places = SE._orig_places


def places():
    items = _h_places()
    preds = _preds_now()
    if not preds:
        return items
    h = A._now_kst().hour
    cand = [it["id"] for it in items if _est_place(it["id"]) and it["id"] in preds]
    counts = _counts_now(cand)
    for it in items:
        pid = it["id"]
        if pid not in preds or not _est_place(pid):
            continue
        e = _entry_from_row(h, preds[pid], counts.get(pid))
        if not e or e["level"] == "gray":
            continue
        it["level"], it["level_source"] = e["level"], "estimated"
        HX._put_items(it, e["factors"])
    return items


# ---------------------------------------------------------------------------
# /now
# ---------------------------------------------------------------------------
_h_now = SE._orig_now


def now(place_id):
    out = _h_now(place_id)
    if not isinstance(out, dict) or not _est_place(place_id):
        return out
    p = PX._extra_places().get(place_id)
    if not p:
        return out
    nk = A._now_kst()
    try:
        hours, _near = PX._estimated_hours(place_id, nk.date(), p)
        e = hours[nk.hour]
    except Exception:
        return out
    if e.get("source") != "estimated" or e.get("level") == "gray":
        return out
    ind = out.setdefault("indoor", {})
    ind.update(level=e["level"], worst_factors=e["worst_factors"], score=e["score"], grade=e["grade"],
               source="estimated", confidence="low", stale=False,
               factors={f: {"value": v.get("value"), "level": v.get("level")} for f, v in e["factors"].items()})
    if e.get("reports_used"):
        ind["reports_used"] = e["reports_used"]
    out["data_available"], out["level_source"] = True, "estimated"
    topic = DX._topic(p["name"])
    names = ", ".join(A.FACTOR_KO.get(f, f) for f in e["worst_factors"] or [])
    lvl = HX.BURDEN_KO.get(e["level"])
    if lvl:
        out["summary_ko"] = (f"{topic} 추정으로는 지금 {names} 때문에 {lvl}. (예측값)"
                             if names and e["level"] != "green" else f"{topic} 추정으로는 지금 {lvl}. (예측값)")
    return out


SE._orig_places, SE._orig_now = places, now
