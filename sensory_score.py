"""감각부담 점수(0~100)와 신호등 색 계산 — 보고서 5.5 ② 로직.

조명 항목: 조도 센서(CdS)는 응답이 느려 형광등 깜빡임을 못 잡는다. 그래서 조명 항목은 센서가 못 재는
"깜빡임"을 카페 이용자 원탭 제보(깜빡여요 yes / 아니요 no)로 채운다. 모든 카페에 적용.
  조명점수 = 10 × (깜빡임 없음 비율)
제보가 적을 때는 ASHRAE 55의 "80% 만족이면 수용 가능" 기준(깜빡임 없음 80%)을 제보 3건만큼 섞어
(베이지안 평균) 한두 명 의견에 색이 뒤집히지 않게 한다.

추정 카페(센서 없음, 가까운 측정 카페 예측을 빌려 씀)의 소음·온도: 빌려 온 값은 그 카페의 실측이 아니므로
그 카페에 있던 이용자의 원탭 제보로 보완한다. 제보를 dB·℃로 바꾸지 않고 항목 점수(0~10)끼리 섞는다.
  소음 제보: 조용해요 quiet 10점 · 보통이에요 ok 5점 · 시끄러워요 loud 0점
  온도 제보: 적당해요 ok 10점 · 추워요 cold / 더워요 hot 0점
  항목점수 = (3 × 추정값 점수 + 제보 점수 합) ÷ (3 + 제보 수)   (제보 3건 이상일 때만. 2건 이하면 추정값 그대로)
추정값을 제보 3건만큼의 무게로 두므로(깜빡임과 같은 3), 제보 3건이면 반반, 9건이면 제보가 3/4을 차지한다.
습도는 사람이 온도와 잘 구분하지 못해 제보를 받지 않고 추정값을 그대로 쓴다.
측정 카페(센서 있음)는 소음·온도 제보를 점수에 섞지 않는다(호출할 때 넘기지 않으면 됨).

온보딩 개인화: 온보딩에서 받은 소리·빛·온도 예민도(각 1~5, 보통 3)로 가중치를 사람마다 조정한다.
  배수 = 응답 ÷ 3, 0.5~2배 안으로 묶음 (1→0.5, 2→0.67, 3→1, 4→1.33, 5→1.67)
  PERSONALIZE_MODE = "burden" (기본, 11/18):
    감각부담 = Σ 가중치 × 배수 × (10 − 항목점수),  점수 = 100 − 10 × 감각부담 (0 아래는 0)
    가중치 합을 1로 다시 나누지 않는다. 그래서 예민한 항목이 나쁠 때만 점수가 더 깎이고,
    좋은 항목(10점)은 배수와 상관없이 영향이 없다. 모두 3이면 논문 가중치 계산과 똑같다.
  PERSONALIZE_MODE = "reweight" (10/30 원안): 기본 가중치 × 배수를 합 1로 다시 나누고 0.5~2배로 묶음.
    한 항목에 예민하다고 답하면 다른 항목 비중이 줄어, 예: 온도에 예민한 사람에게 시끄러운 카페가 더 초록이 됨.
  안전 규칙·항목 점수 기준은 바꾸지 않는다.
  예민도는 건강 관련 정보라 서버에 저장하지 않고, 앱이 요청할 때마다 넘겨 계산에만 쓴다.

가중치는 Heinzerling 외(2013) 표 7(음환경 0.39, 빛 0.29, 공기질 0.20, 열환경 0.12)에서
"값이 있는 항목만" 남겨 합이 1이 되게 다시 나눈다.
  깜빡임 제보 충분(최근 30일 3건 이상): 소음 0.49 · 조명 0.36 · 온습도 0.15
  깜빡임 제보 부족:                     소음 0.76 · 온습도 0.24

앱 API에서 쓰는 법
    from sensory_score import sensory_score
    from user_reports import light_score_for, report_counts   # 제보 집계 (DB)
    ls = light_score_for(conn, "cafe-A")                      # 제보 부족하면 None
    r = sensory_score(noise_db=70.2, temp=23.1, humidity=55, light_score=ls)          # 측정 카페
    nr = report_counts(conn, "cafe-C", "noise", date, hour)  # 추정 카페만
    tr = report_counts(conn, "cafe-C", "temp", date, hour)
    r = sensory_score(70.2, 23.1, 55, ls, noise_reports=nr, temp_reports=tr)
    r = sensory_score(70.2, 23.1, 55, ls, sensitivity={"noise": 5, "light": 3, "thermal": 2})   # 온보딩 개인화
    r["color"]  # "green" / "yellow" / "red" / "gray"
python sensory_score.py 로 실행하면 계산 예와 경계값을 출력한다.
"""
# Heinzerling 외(2013) Table 7 제안 가중치 (CBE 설문 52,980명 회귀계수를 합 1로 정규화)
BASE_WEIGHTS = {"noise": 0.39, "light": 0.29, "iaq": 0.20, "thermal": 0.12}

# 항목 점수 기준 (10점 범위, 0점 위치) — 보고서 5.5 ② "우리가 정한 부분" 표와 같음
NOISE_BEST, NOISE_WORST = 65.0, 80.0            # Rindel(2010) 대화 "만족" 상한 / Rindel(2018) "분명히 불쾌"
TEMP_OK, TEMP_ZERO = (18.5, 24.5), (15.5, 27.5)          # Chiang 외(2001) "건강" 범위, 폭의 절반 바깥 0점
HUM_OK, HUM_ZERO = (43.0, 67.0), (31.0, 79.0)

# Larsen 외(2020) IEQ-Compass 7등급 경계 (만점 대비 %)
GRADE_CUTS = (85, 75, 65, 55, 45, 35)
SAFETY_STEPS = 2                       # 전체 등급은 가장 나쁜 영역 등급보다 2단계 넘게 좋을 수 없음
COLOR_OF_GRADE = {1: "green", 2: "green", 3: "yellow", 4: "yellow", 5: "red", 6: "red", 7: "red"}


# 깜빡임 원탭 제보 → 조명점수
LIGHT_MIN_REPORTS = 3   # 최근 제보가 이 수 미만이면 조명 항목을 빼고 계산 (기존 신뢰도 high 기준 표본 3과 같음)
PRIOR_OK = 0.80         # 사전 "깜빡임 없음" 비율: ASHRAE 55의 "재실자 80% 만족이면 수용 가능" 기준
PRIOR_N = 3             # 사전값을 제보 3건만큼의 무게로 섞음. 제보가 적을 때 한두 명 의견에 색이 뒤집히지 않게

# 추정 카페 소음·온도 원탭 제보 → 항목 점수 (0~10 척도의 양 끝과 가운데. 팀이 정한 값)
REPORT_VALUES = {
    "flicker": ("yes", "no"),
    "noise": ("quiet", "ok", "loud"),
    "temp": ("cold", "ok", "hot"),
}
NOISE_REPORT_SCORE = {"quiet": 10.0, "ok": 5.0, "loud": 0.0}
TEMP_REPORT_SCORE = {"ok": 10.0, "cold": 0.0, "hot": 0.0}
REPORT_PRIOR_N = PRIOR_N   # 추정값을 제보 3건만큼의 무게로 둠


def light_score_from_flicker(yes, no, min_n=LIGHT_MIN_REPORTS):
    """깜빡임 제보 수 → 조명점수(0~10) = 10 × (깜빡임 없음 비율).
    비율 = (no + 3×0.8) ÷ (전체 + 3). 제보가 쌓일수록 실제 비율에 가까워짐. 3건 미만이면 None."""
    n = yes + no
    if n < min_n:
        return None
    return round(10.0 * (no + PRIOR_N * PRIOR_OK) / (n + PRIOR_N), 2)


def blend_with_reports(est_score, counts, table, prior_n=REPORT_PRIOR_N, min_n=LIGHT_MIN_REPORTS):
    """추정값 점수(0~10)와 제보 점수를 섞는다. counts 예: {"quiet": 1, "ok": 0, "loud": 4}.
    = (3 × 추정값 점수 + 제보 점수 합) ÷ (3 + 제보 수).
    깜빡임과 같이 제보가 3건 미만이면 섞지 않고 추정값 점수를 그대로 쓴다(한두 명 의견에 색이 뒤집히지 않게).
    추정값이 없으면(예측 실패) 제보 3건 이상일 때만 제보 평균, 아니면 None."""
    counts = {k: int(v) for k, v in (counts or {}).items() if k in table and v}
    n = sum(counts.values())
    if n < min_n:
        return est_score
    total = sum(table[k] * v for k, v in counts.items())
    if est_score is None:
        return total / n
    return (prior_n * est_score + total) / (prior_n + n)


def weights(use_light):
    keys = ["noise", "light", "thermal"] if use_light else ["noise", "thermal"]
    s = sum(BASE_WEIGHTS[k] for k in keys)
    # 보고서 표기와 같게 소수 둘째 자리로 반올림 (0.49+0.36+0.15 = 0.76+0.24 = 1.00)
    return {k: round(BASE_WEIGHTS[k] / s, 2) for k in keys}


# 온보딩 예민도 → 가중치 배수
SENS_NEUTRAL = 3                 # 보통. 이 값이면 논문 가중치 그대로
SENS_RANGE = (1, 5)
WEIGHT_BOUNDS = (0.5, 2.0)       # 최종 가중치는 기본 가중치의 0.5~2배 안 (10/30 결정)
PERSONALIZE_MODE = "burden"     # "burden"(권장) 또는 "reweight"(10/30 원안)
SENS_ALIAS = {"sound": "noise", "noise": "noise", "light": "light", "flicker": "light",
              "temp": "thermal", "thermal": "thermal"}


def sensitivity_multipliers(keys, sensitivity=None):
    """온보딩 예민도 → 항목별 배수 = 응답 ÷ 3 (0.5~2배로 묶음). 없는 항목은 1."""
    m = {k: 1.0 for k in keys}
    for k, v in (sensitivity or {}).items():
        k = SENS_ALIAS.get(str(k).lower())
        if k in m and v is not None:
            v = max(SENS_RANGE[0], min(SENS_RANGE[1], float(v)))
            m[k] = max(WEIGHT_BOUNDS[0], min(WEIGHT_BOUNDS[1], v / SENS_NEUTRAL))
    return m


def personal_weights(base, sensitivity=None):
    """base: 이번 계산에 쓰는 항목의 기본 가중치(예: {"noise": 0.49, "light": 0.36, "thermal": 0.15}).
    sensitivity: 온보딩 예민도 {"noise": 1~5, "light": 1~5, "thermal": 1~5}. 없는 항목은 3(보통).
    → 기본 × (응답 ÷ 3)을 합 1로 다시 나누고, 각 가중치를 기본값의 0.5~2배 안으로 묶는다."""
    keys = list(base)
    tot = sum(base.values())
    b = {k: base[k] / tot for k in keys}
    lv = {k: SENS_NEUTRAL for k in keys}
    for k, v in (sensitivity or {}).items():
        k = SENS_ALIAS.get(str(k).lower())
        if k in lv and v is not None:
            lv[k] = max(SENS_RANGE[0], min(SENS_RANGE[1], float(v)))
    raw = {k: b[k] * lv[k] / SENS_NEUTRAL for k in keys}
    lo = {k: WEIGHT_BOUNDS[0] * b[k] for k in keys}
    hi = {k: min(1.0, WEIGHT_BOUNDS[1] * b[k]) for k in keys}
    fixed = {}
    for _ in range(len(keys) + 1):
        free = [k for k in keys if k not in fixed]
        if not free:
            break
        rem = 1.0 - sum(fixed.values())
        fs = sum(raw[k] for k in free)
        cur = {k: raw[k] / fs * rem for k in free}
        out = {k: (lo[k] if cur[k] < lo[k] else hi[k]) for k in free
               if cur[k] < lo[k] - 1e-9 or cur[k] > hi[k] + 1e-9}
        if not out:
            fixed.update(cur)
            break
        fixed.update(out)
    t = sum(fixed.values())
    return {k: fixed[k] / t for k in keys}


def _one_sided(x, best, worst):
    """클수록 나쁜 항목: best 이하 10점, worst 이상 0점, 사이는 직선."""
    return max(0.0, min(10.0, 10.0 * (worst - x) / (worst - best)))


def _two_sided(x, ok, zero):
    """범위 안 10점, 바깥은 0점 위치까지 직선으로 감소."""
    lo, hi = ok
    if x < lo:
        return _one_sided(-x, -lo, -zero[0])
    if x > hi:
        return _one_sided(x, hi, zero[1])
    return 10.0


def item_scores(noise_db=None, temp=None, humidity=None, light_score=None, noise_reports=None, temp_reports=None):
    s = {}
    noise = _one_sided(noise_db, NOISE_BEST, NOISE_WORST) if noise_db is not None else None
    if noise_reports:
        noise = blend_with_reports(noise, noise_reports, NOISE_REPORT_SCORE)
    if noise is not None:
        s["noise"] = noise
    if light_score is not None:
        s["light"] = max(0.0, min(10.0, float(light_score)))
    t = _two_sided(temp, TEMP_OK, TEMP_ZERO) if temp is not None else None
    if temp_reports:
        t = blend_with_reports(t, temp_reports, TEMP_REPORT_SCORE)
    th = [v for v in (t, _two_sided(humidity, HUM_OK, HUM_ZERO) if humidity is not None else None) if v is not None]
    if th:
        s["thermal"] = sum(th) / len(th)   # 온도·습도 점수 평균 (한쪽만 있으면 그 값)
    return s


# 항목 색 (11/18 저녁부터 종합 색 = 소음·온도·습도 중 가장 나쁜 항목 색, 조명 제외)
# 소음은 Rindel 경계를 그대로 쓴다: 71 dB 이하 초록(1 m 대화 "충분", Rindel 2010·2018 "71 dB를 넘으면 안 됨"),
# 80 dB 이상 빨강(Rindel 2018 "매우 시끄럽고 분명히 불쾌"). 점수로 바꾸면 6.0점 이상 초록, 0점 빨강.
# 온도·습도는 항목 점수 75% 이상 초록, 55% 이상 노랑(Larsen 7등급을 3색으로 묶은 경계, 팀이 정한 값).
NOISE_GREEN_MAX_DB, NOISE_RED_MIN_DB = 71.0, 80.0
ITEM_CUTS = {
    "noise": (_one_sided(NOISE_GREEN_MAX_DB, NOISE_BEST, NOISE_WORST), 0.0),   # (초록 이상, 이 값 이하 빨강) = (6.0, 0)
    "temp": (7.5, 5.5),
    "humidity": (7.5, 5.5),
}


def item_level(item, score):
    """항목 점수(0~10) → green / yellow / red. 점수가 없으면 gray."""
    if score is None:
        return "gray"
    g, r = ITEM_CUTS[item]
    if score >= g - 1e-9:
        return "green"
    if item == "noise":
        return "red" if score <= r + 1e-9 else "yellow"
    return "yellow" if score >= r - 1e-9 else "red"


def item_levels(noise_db=None, temp=None, humidity=None, noise_reports=None, temp_reports=None):
    """항목별 색과 종합 색(가장 나쁜 색). 제보는 추정 카페(has_sensor=false)에서만 넘긴다."""
    n = _one_sided(noise_db, NOISE_BEST, NOISE_WORST) if noise_db is not None else None
    if noise_reports:
        n = blend_with_reports(n, noise_reports, NOISE_REPORT_SCORE)
    t = _two_sided(temp, TEMP_OK, TEMP_ZERO) if temp is not None else None
    if temp_reports:
        t = blend_with_reports(t, temp_reports, TEMP_REPORT_SCORE)
    h = _two_sided(humidity, HUM_OK, HUM_ZERO) if humidity is not None else None
    lv = {"noise": item_level("noise", n), "temp": item_level("temp", t), "humidity": item_level("humidity", h)}
    rank = {"red": 3, "yellow": 2, "green": 1}
    known = [v for v in lv.values() if v in rank]
    lv["color"] = max(known, key=rank.get) if known else "gray"
    lv["scores"] = {k: (round(v, 2) if v is not None else None) for k, v in (("noise", n), ("temp", t), ("humidity", h))}
    return lv


def grade_of(pct):
    for g, cut in enumerate(GRADE_CUTS, start=1):
        if pct >= cut:
            return g
    return 7


def sensory_score(noise_db=None, temp=None, humidity=None, light_score=None, noise_reports=None, temp_reports=None,
                  sensitivity=None):
    """감각부담 점수와 색. light_score는 원탭 제보 조명점수(0~10), 제보 부족이면 None.
    noise_reports·temp_reports는 추정 카페에서만 넘기는 제보 개수(dict). 측정 카페는 넘기지 않는다.
    sensitivity는 온보딩 예민도 {"noise", "light", "thermal": 1~5}. 없으면 논문 가중치 그대로.
    값이 없는 항목은 빼고 남은 항목으로 가중치를 다시 나눈다(① 기준의 회색 처리와 같은 원칙)."""
    s = item_scores(noise_db, temp, humidity, light_score, noise_reports, temp_reports)
    if not s:
        return {"score": None, "grade": None, "color": "gray", "items": {}, "weights": {}}
    w = weights("light" in s)
    s = {k: v for k, v in s.items() if k in w}
    if sensitivity and PERSONALIZE_MODE == "reweight":
        w = personal_weights({k: w[k] for k in s}, sensitivity)
    wsum = sum(w[k] for k in s)
    if sensitivity and PERSONALIZE_MODE == "burden":
        m = sensitivity_multipliers(list(s), sensitivity)
        burden = sum(w[k] / wsum * m[k] * (10.0 - s[k]) for k in s)
        score = max(0.0, 100.0 - 10.0 * burden)
    else:
        score = 10.0 * sum(w[k] / wsum * s[k] for k in s)
    g_total = grade_of(score)
    g_worst = max(grade_of(10.0 * v) for v in s.values())
    grade = max(g_total, g_worst - SAFETY_STEPS)
    used = {k: sum((v or {}).values()) for k, v in (("noise", noise_reports), ("temp", temp_reports)) if v}
    return {"score": round(score, 1), "grade": grade, "grade_raw": g_total, "worst_item_grade": g_worst,
            "capped": grade != g_total, "color": COLOR_OF_GRADE[grade],
            "items": {k: round(v, 2) for k, v in s.items()},
            "weights": {k: round(w[k], 3) for k in s}, "reports_used": used, "personalized": bool(sensitivity)}


def boundary_db(light_score=None, color="green", sensitivity=None):
    """온습도 만점(조도는 light_score)일 때 그 색이 나오는 최대 소음(dB). 0.01 dB 단위 탐색."""
    ok = {"green": ("green",), "yellow": ("green", "yellow")}[color]
    db = NOISE_BEST
    while db <= NOISE_WORST + 1:
        if sensory_score(db, 21.5, 55, light_score, sensitivity=sensitivity)["color"] not in ok:
            return round(db - 0.01, 2)
        db += 0.01
    return None


if __name__ == "__main__":
    print("항목 색 (종합 색 = 가장 나쁜 항목 색). 소음 경계:", ITEM_CUTS["noise"])
    for db in (68, 70.5, 71, 71.5, 75, 79.9, 80, 82):
        print(f"  소음 {db} dB → {item_level('noise', _one_sided(db, NOISE_BEST, NOISE_WORST))}")
    for nr in ({"loud": 3}, {"loud": 6}, {"quiet": 3}):
        print(f"  추정 68 dB + 제보 {nr} → {item_levels(68, 23, 55, noise_reports=nr)}")
        print(f"  추정값 없음 + 제보 {nr} → {item_levels(None, None, None, noise_reports=nr)}")
    print("")
    print("가중치  깜빡임 제보 있음:", weights(True), " 깜빡임 제보 부족:", weights(False))
    print("\n깜빡임 제보 → 조명점수 (yes, no)")
    for c in ((0, 10), (2, 8), (5, 5), (3, 0), (10, 0), (1, 1)):
        print(f"  {c} → {light_score_from_flicker(*c)}")
    print("\n계산 예 (23℃·55%)")
    for db in (68, 72.5, 80):
        for ls in (10, 7, 4, None):
            r = sensory_score(db, 23, 55, ls)
            print(f"  소음 {db} dB, 조명점수 {ls} → {r['score']}점, {r['grade']}등급"
                  f"{' (안전규칙)' if r['capped'] else ''}, {r['color']}")
    print("\n온습도 만점일 때 색이 바뀌는 소음")
    for ls in (10, 8, 7, 5, 4, 2, None):
        print(f"  조명점수 {ls}: 초록 ≤ {boundary_db(ls, 'green')} dB, 노랑 ≤ {boundary_db(ls, 'yellow')} dB")
    print("\n추정 카페: 추정 소음 68 dB(점수 8.0), 23℃·55%, 깜빡임 제보 부족")
    for nr in ({}, {"loud": 2}, {"loud": 3}, {"loud": 6}, {"ok": 3}, {"quiet": 3}, {"loud": 2, "ok": 2, "quiet": 1}):
        r = sensory_score(68, 23, 55, None, noise_reports=nr)
        print(f"  소음 제보 {nr or '없음'} → 소음점수 {r['items']['noise']}, {r['score']}점, {r['color']}")
    print("\n추정 카페: 추정 21℃(온도 10점)·55%, 추정 소음 68 dB")
    for tr in ({}, {"cold": 2}, {"cold": 3}, {"cold": 9}):
        r = sensory_score(68, 21, 55, None, temp_reports=tr)
        print(f"  온도 제보 {tr or '없음'} → 온습도점수 {r['items']['thermal']}, {r['score']}점, {r['color']}")
    print("\n온보딩 개인화 계산 예 (점수 색, *는 안전 규칙). 열: 70.5dB·23℃·조명9.5 | 71.5dB·23℃·조명9.5 | "
          "70.5dB·23℃·제보부족 | 68dB·26.5℃·조명9.5 | 68dB·26.5℃·제보부족 | 68dB·23℃·조명5.7")
    for mode in ("burden", "reweight"):
        PERSONALIZE_MODE = mode
        print(f"  [{mode}]")
        for name, sv in (("보통 3,3,3", None), ("소리 예민 5,3,3", {"noise": 5}), ("소리만 예민 5,1,1", {"noise": 5, "light": 1, "thermal": 1}),
                         ("소리 둔감 1,3,3", {"noise": 1}), ("빛 예민 3,5,3", {"light": 5}), ("온도 예민 3,3,5", {"thermal": 5}),
                         ("모두 예민 5,5,5", {"noise": 5, "light": 5, "thermal": 5})):
            row = []
            for db, t, ls in ((70.5, 23, 9.54), (71.5, 23, 9.54), (70.5, 23, None), (68, 26.5, 9.54), (68, 26.5, None), (68, 23, 5.69)):
                r = sensory_score(db, t, 55, ls, sensitivity=sv)
                row.append(f"{r['score']} {r['color']}{'*' if r['capped'] else ''}")
            print(f"    {name}: " + " | ".join(row))
    PERSONALIZE_MODE = "burden"
    print("\n[burden] 예민도(소리, 빛, 온도) → 온습도 만점일 때 초록이 나오는 소음 상한 (조명 9.5 / 깜빡임 제보 부족)")
    for sv in ((3, 3, 3), (4, 3, 3), (5, 3, 3), (2, 3, 3), (1, 3, 3), (3, 5, 3), (5, 5, 5)):
        d = dict(zip(("noise", "light", "thermal"), sv))
        print(f"  {sv}: {boundary_db(9.54, 'green', d)} dB / {boundary_db(None, 'green', d)} dB")
    print("\n[reweight] 예민도(소리, 빛, 온도) → 다시 나눈 가중치 (깜빡임 제보 있음 / 부족)")
    for sv in ((3, 3, 3), (5, 3, 3), (1, 3, 3), (3, 3, 5), (5, 5, 5), (5, 1, 1)):
        d = dict(zip(("noise", "light", "thermal"), sv))
        a = personal_weights(weights(True), d)
        b = personal_weights(weights(False), d)
        print(f"  {sv}: {{{', '.join(f'{k} {v:.3f}' for k, v in a.items())}}} / "
              f"{{{', '.join(f'{k} {v:.3f}' for k, v in b.items())}}}")
