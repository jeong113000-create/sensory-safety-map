"""
차담·카페쿠피 영업시간 (2026-11-21).

- 차담 by 갤러리 스퀘어(kakao-1795099660): 12~18시, 휴무 없음
- 카페쿠피(kakao-92860343): 9~20시, 수요일 휴무
영업시간 밖(휴무일 포함)은 /places·/now·/indoor 모두 회색(gray), score·grade null.
/places·/now 에 open_hours·open_now·closed_days, /indoor 시간 칸에 closed 를 붙임 (센서 카페와 같은 형식).
값(value)은 참고용으로 그대로 둠.

설치: app_api.py 맨 끝 `import final_ext` 아래에 `import hours2_ext` 한 줄.
되돌리기: 그 한 줄만 지우고 재시작.
"""

import app_api as A
import demo_ext as DX
import hours_ext as HX
import sens_ext as SE

# id: (여는 시, 닫는 시, 휴무 요일 집합)  요일: 월0 화1 수2 목3 금4 토5 일6.  여는 시 <= hour < 닫는 시 만 영업
HOURS2 = {
    "kakao-1795099660": (12, 18, set()),
    "kakao-92860343": (9, 20, {2}),
}
DAY_KO = "월화수목금토일"
DAY_EN = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _open(pid, d, h):
    o, c, off = HOURS2[pid]
    return d.weekday() not in off and o <= h < c


def _hours_info(pid):
    o, c, off = HOURS2[pid]
    return {"open_hours": [o, c], "closed_days": [DAY_EN[i] for i in sorted(off)]}


def _hours_text(pid, d=None):
    o, c, off = HOURS2[pid]
    t = f"영업 {o}시~{c}시"
    if off:
        t += ", " + "·".join(DAY_KO[i] for i in sorted(off)) + "요일 휴무"
    return t


def _gray_factors(fs):
    for f in (fs or {}).values():
        if isinstance(f, dict):
            f["level"] = "gray"


_p_places, _p_now, _p_indoor = SE._orig_places, SE._orig_now, SE._orig_indoor


def places():
    items = _p_places()
    nk = A._now_kst()
    for it in items:
        pid = it.get("id")
        if pid not in HOURS2:
            continue
        it.update(_hours_info(pid))
        it["open_now"] = _open(pid, nk.date(), nk.hour)
        if not it["open_now"]:
            it["level"] = "gray"
            HX._put_items(it, None)
    return items


def now(place_id):
    out = _p_now(place_id)
    if place_id not in HOURS2 or not isinstance(out, dict):
        return out
    nk = A._now_kst()
    out.update(_hours_info(place_id))
    out["open_now"] = _open(place_id, nk.date(), nk.hour)
    if not out["open_now"]:
        ind = out.setdefault("indoor", {})
        ind["level"], ind["worst_factors"], ind["score"], ind["grade"] = "gray", [], None, None
        _gray_factors(ind.get("factors"))
        topic = DX._topic(HX._name(place_id, out))
        off_today = nk.date().weekday() in HOURS2[place_id][2]
        why = "오늘은 휴무예요" if off_today else "지금 영업시간이 아니에요"
        s = f"{topic} {why} ({_hours_text(place_id)})."
        if out.get("is_demo"):
            s += " (시연용 가정값)"
        out["summary_ko"] = s
    return out


def indoor(place_id, date):
    out = _p_indoor(place_id, date)
    if place_id not in HOURS2 or not isinstance(out, dict):
        return out
    d = A._parse_date(date)
    out.update(_hours_info(place_id))
    for e in out.get("hours") or []:
        if _open(place_id, d, e["hour"]):
            e["closed"] = False
        else:
            HX._gray_hour(e)
    return out


SE._orig_places, SE._orig_now, SE._orig_indoor = places, now, indoor
