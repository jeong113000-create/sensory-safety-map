"""사용자 원탭 제보 — 앱 API(FastAPI)에 붙이는 모듈.

제보 항목 (11/18부터 3개)
  flicker  깜빡임    yes(깜빡여요) / no(안 깜빡여요)          모든 카페. 조도 센서(CdS)가 깜빡임을 못 잡아서 받음
  noise    소음      quiet(조용해요) / ok(보통이에요) / loud(시끄러워요)   추정 카페만(앱에서 버튼을 추정 카페에만 보임)
  temp     온도      cold(추워요) / ok(적당해요) / hot(더워요)            추정 카페만
추정 카페는 센서가 없어 가까운 측정 카페의 예측을 빌려 쓰므로, 그 카페에 있던 사람의 제보로 소음·온도를 보완한다.
습도는 사람이 온도와 잘 구분하지 못해 받지 않는다. 위치(카페 100m 이내) 검증은 앱에서만 하고 서버로 보내지 않는다.

설치 (API 서버, main.py와 같은 폴더에 이 파일과 sensory_score.py를 둔다)
  1) python3 user_reports.py --create      표 만들기/고치기 (예전 표가 있어도 그대로 이어서 씀)
  2) main.py에 두 줄 추가 (이미 했으면 생략):
         from user_reports import router as report_router
         app.include_router(report_router)
  3) sudo systemctl restart fastapi
  4) 매일 파기 작업 등록 (crontab -e 에 한 줄, 이미 했으면 생략):
         30 3 * * * cd /home/ubuntu && /usr/bin/python3 user_reports.py --purge >> purge.log 2>&1

API
  POST   /report              {"place_id", "user_id", "item": "flicker"|"noise"|"temp", "value": 위 값}
                              예전 형식 {"place_id", "user_id", "flicker": "yes"|"no"}도 그대로 받음
                              200 {"message": ...} / 409 같은 사람·장소·항목·날짜·시간대 중복 / 422 형식 오류
  GET    /report/summary      ?place_id=cafe-C&hour=14 →
                              {"place_id", "flicker_score": 0~1|null, "report_count",      ← 예전과 같은 칸
                               "noise": {"quiet","ok","loud","count"}, "temp": {"cold","ok","hot","count"}}
  GET    /report/mine         ?user_id=... → 내 제보 목록 (열람 요청)
  DELETE /report/mine         ?user_id=... → 내 제보 전부 삭제 (삭제 요청)

신호등 계산 (정지연, main.py) — 추정 카페(predictions.source = 'estimated')일 때만 소음·온도 제보를 넘긴다
    ls = light_score_for(conn, place_id)
    if 추정 카페:
        nr = report_counts(conn, place_id, "noise", hour=hour)
        tr = report_counts(conn, place_id, "temp", hour=hour)
        r = sensory_score(noise, temp, humidity, ls, noise_reports=nr, temp_reports=tr)
    else:
        r = sensory_score(noise, temp, humidity, ls)

어떤 제보를 세는가 (같은 사람이 같은 카페·같은 날 여러 번 눌렀으면 그날 마지막 1건만)
  flicker  최근 30일, 시간 무관 (조명 기구 성질이라 시간에 따라 안 바뀜)
  noise    최근 30일, 같은 요일유형(평일 / 주말·공휴일), 보려는 시각 ±1시간
  temp     최근 7일, 보려는 시각 ±1시간 (냉난방·계절에 따라 바뀌어 기간을 짧게)

DB 접속 정보: 환경변수 RDS_HOST·RDS_USER·RDS_PASSWORD·RDS_DB, 없으면 main.py의 같은 이름 상수를 그대로 씀.

보관 방침 (개인정보 처리방침에 쓸 내용)
  - user_id는 원문을 저장하지 않고 SHA-256 해시 앞 16자리만 저장한다.
  - 제보 원본(해시 포함)은 90일 보관 후 파기한다. 파기 전에 카페·항목·날짜·시간대·값별 개수만 집계표로 옮기고,
    집계표에는 이용자 식별값이 없다. (점수 계산에는 최근 30일만 쓰고, 나머지는 중복·악용 확인용)
"""
import hashlib
import os
from datetime import date as Date, datetime, timedelta, timezone
from typing import Literal, Optional

import pymysql
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sensory_score import LIGHT_MIN_REPORTS, REPORT_VALUES, light_score_from_flicker

try:
    import holidays
    KR_HOLIDAYS = holidays.KR()
except Exception:      # API 서버에 holidays가 없으면 토·일만 주말로 봄
    KR_HOLIDAYS = {}

TABLE = os.environ.get("REPORT_TABLE", "user_reports")
DAILY_TABLE = os.environ.get("REPORT_DAILY_TABLE", "user_reports_daily")   # 파기 후 남기는 집계표 (식별값 없음)
WINDOW_DAYS = int(os.environ.get("REPORT_WINDOW_DAYS", 30))   # 깜빡임·소음은 최근 30일 제보로 계산
TEMP_WINDOW_DAYS = int(os.environ.get("REPORT_TEMP_WINDOW_DAYS", 7))   # 온도는 최근 7일
HOUR_SPAN = int(os.environ.get("REPORT_HOUR_SPAN", 1))        # 소음·온도: 보려는 시각 ±1시간 제보만
RETENTION_DAYS = int(os.environ.get("REPORT_RETENTION_DAYS", 90))   # 원본 보관 기간
MIN_REPORTS = LIGHT_MIN_REPORTS                               # 3건 미만이면 flicker_score = null
KST = timedelta(hours=9)

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  place_id    VARCHAR(64)  NOT NULL,
  user_hash   CHAR(16)     NOT NULL,          -- 기기 ID의 SHA-256 해시 앞 16자리 (원래 ID는 저장 안 함)
  item        VARCHAR(16)  NOT NULL DEFAULT 'flicker',   -- flicker / noise / temp
  value       VARCHAR(16)  NOT NULL,          -- yes·no / quiet·ok·loud / cold·ok·hot
  report_date DATE         NULL,              -- 한국 날짜 (중복 차단용)
  report_hour TINYINT      NULL,              -- 한국 시각 0~23 (중복 차단용)
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,   -- UTC (sensor_data와 같음)
  INDEX idx_place_time (place_id, item, created_at),
  INDEX idx_user (user_hash),
  UNIQUE KEY uq_once (user_hash, place_id, item, report_date, report_hour)
)"""

CREATE_DAILY_SQL = f"""
CREATE TABLE IF NOT EXISTS {DAILY_TABLE} (
  place_id    VARCHAR(64) NOT NULL,
  item        VARCHAR(16) NOT NULL,
  report_date DATE        NOT NULL,
  report_hour TINYINT     NOT NULL,
  value       VARCHAR(16) NOT NULL,
  cnt         INT         NOT NULL DEFAULT 0,
  PRIMARY KEY (place_id, item, report_date, report_hour, value)
)"""


def _setting(name, default=None):
    """환경변수 → 없으면 main.py에 적힌 같은 이름 상수(RDS_HOST 등)를 쓴다. 기존 main.py를 고치지 않아도 되게."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        import main
        return getattr(main, name, default)
    except Exception:
        return default


def connect():
    return pymysql.connect(host=_setting("RDS_HOST"), user=_setting("RDS_USER", "admin"),
                           password=_setting("RDS_PASSWORD"), database=_setting("RDS_DB", "sensory_db"),
                           port=int(_setting("RDS_PORT", 3306)), connect_timeout=5)


def _hash(user_id):
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _day_type(d):
    return "weekend" if d.weekday() >= 5 or d in KR_HOLIDAYS else "weekday"


def _columns(cur, table):
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
                (table,))
    return {r[0] for r in cur.fetchall()}


def ensure_tables(conn):
    """새로 만들거나 예전 버전 표를 고친다. 제보 원본 행은 그대로 둔다.
    예전 집계표(yes_count·no_count 칸)는 비어 있으면 새 모양으로 다시 만들고,
    행이 있으면 {DAILY_TABLE}_yesno 로 이름만 바꿔 보관한 뒤 새 표를 만든다."""
    with conn.cursor() as cur:
        cur.execute(CREATE_SQL)
        cols = _columns(cur, TABLE)
        if "report_date" not in cols:
            cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN report_date DATE NULL")
        if "report_hour" not in cols:
            cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN report_hour TINYINT NULL")
        cur.execute("SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
                    (TABLE,))
        idx = {r[0] for r in cur.fetchall()}
        if "uq_once" not in idx:
            # 예전 행은 report_date가 비어 있어(NULL) 서로 충돌하지 않음
            cur.execute(f"ALTER TABLE {TABLE} ADD UNIQUE KEY uq_once (user_hash, place_id, item, report_date, report_hour)")
        if "idx_user" not in idx:
            cur.execute(f"ALTER TABLE {TABLE} ADD INDEX idx_user (user_hash)")

        if "yes_count" in _columns(cur, DAILY_TABLE):
            cur.execute(f"SELECT COUNT(*) FROM {DAILY_TABLE}")
            if cur.fetchone()[0] == 0:
                cur.execute(f"DROP TABLE {DAILY_TABLE}")
            else:
                cur.execute(f"RENAME TABLE {DAILY_TABLE} TO {DAILY_TABLE}_yesno")
        cur.execute(CREATE_DAILY_SQL)
    conn.commit()


def report_counts(conn, place_id, item, hour=None, on=None, now=None):
    """제보 개수를 값별로 센다. 예: report_counts(conn, "cafe-C", "noise", hour=14)
    → {"quiet": 1, "ok": 0, "loud": 4}
      flicker: 최근 30일, 시간 무관
      noise:   최근 30일, on(기본 오늘, 한국 날짜)과 같은 요일유형, hour ±1시간 (hour가 없으면 시간 무관)
      temp:    최근 7일, hour ±1시간
    같은 사람이 같은 카페에 같은 날 여러 시간대에 눌렀으면 그날 마지막 1건만 센다(한 사람 의견이 부풀지 않게)."""
    now = now or _utcnow()
    days = TEMP_WINDOW_DAYS if item == "temp" else WINDOW_DAYS
    with conn.cursor() as cur:
        cur.execute(f"SELECT user_hash, value, created_at FROM {TABLE} "
                    f"WHERE place_id=%s AND item=%s AND created_at >= %s ORDER BY created_at",
                    (place_id, item, now - timedelta(days=days)))
        rows = cur.fetchall()
    on = on or (now + KST).date()
    latest = {}
    for user, value, t in rows:
        k = t + KST
        if item != "flicker" and hour is not None:
            gap = abs(k.hour - int(hour))
            if min(gap, 24 - gap) > HOUR_SPAN:
                continue
        if item == "noise" and _day_type(k.date()) != _day_type(on):
            continue
        latest[(user, k.date())] = value   # 시간순이라 마지막 값이 남음
    return {v: sum(x == v for x in latest.values()) for v in REPORT_VALUES[item]}


def flicker_counts(conn, place_id, now=None):
    """최근 30일 깜빡임 제보를 (yes 수, no 수)로 센다. (예전 코드 호환용)"""
    c = report_counts(conn, place_id, "flicker", now=now)
    return c["yes"], c["no"]


def light_score_for(conn, place_id, hour=None):
    """신호등 계산용 조명점수(0~10, 깜빡임 없을수록 높음). 제보 3건 미만이면 None. hour는 호환용(미사용)."""
    return light_score_from_flicker(*flicker_counts(conn, place_id))


def summary(yes, no):
    n = yes + no
    return {"flicker_score": round(yes / n, 2) if n >= MIN_REPORTS else None, "report_count": n}


def purge(conn, now=None):
    """RETENTION_DAYS일 지난 원본을 카페·항목·날짜·시간대·값별 개수로 집계표에 옮긴 뒤 원본(해시 포함)을 지운다."""
    cutoff = (now or _utcnow()) - timedelta(days=RETENTION_DAYS)
    with conn.cursor() as cur:
        cur.execute(f"""INSERT INTO {DAILY_TABLE} (place_id, item, report_date, report_hour, value, cnt)
                        SELECT place_id, item, DATE(DATE_ADD(created_at, INTERVAL 9 HOUR)),
                               HOUR(DATE_ADD(created_at, INTERVAL 9 HOUR)), value, COUNT(*)
                        FROM {TABLE} WHERE created_at < %s
                        GROUP BY place_id, item, DATE(DATE_ADD(created_at, INTERVAL 9 HOUR)),
                                 HOUR(DATE_ADD(created_at, INTERVAL 9 HOUR)), value
                        ON DUPLICATE KEY UPDATE cnt = cnt + VALUES(cnt)""", (cutoff,))
        n = cur.execute(f"DELETE FROM {TABLE} WHERE created_at < %s", (cutoff,))
    conn.commit()
    return n


# ---------------- FastAPI 엔드포인트 ----------------
router = APIRouter()
ITEM_NAME = {"flicker": "깜빡임", "noise": "소음", "temp": "온도"}


class Report(BaseModel):
    place_id: str
    user_id: str
    item: Optional[Literal["flicker", "noise", "temp"]] = None
    value: Optional[str] = None
    flicker: Optional[Literal["yes", "no"]] = None   # 예전 형식 호환


@router.post("/report")
def post_report(r: Report):
    if not r.user_id.strip() or not r.place_id.strip():
        raise HTTPException(422, "place_id, user_id가 비어 있어요")
    item, value = (r.item, r.value) if r.item else ("flicker", r.flicker)
    if value not in REPORT_VALUES[item]:
        raise HTTPException(422, f"{item} 값은 {' / '.join(REPORT_VALUES[item])} 중 하나여야 해요")
    now = _utcnow()
    kst = now + KST
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {TABLE} (place_id, user_hash, item, value, report_date, report_hour, created_at) "
                        f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (r.place_id, _hash(r.user_id), item, value, kst.date(), kst.hour, now))
        conn.commit()
    except pymysql.err.IntegrityError:
        # 같은 사람·장소·항목·날짜·시간대 중복 (앱을 지우고 다시 깔아도 user_id가 같으면 막힘)
        raise HTTPException(409, f"이 시간대에는 이미 {ITEM_NAME[item]} 제보를 했어요")
    finally:
        conn.close()
    return {"message": "제보가 반영됐어요"}


@router.get("/report/summary")
def report_summary(place_id: str, hour: Optional[int] = None):
    """앱 표시용. 소음·온도 개수는 추정 카페에서만 화면에 보이면 된다(측정 카페는 보통 0)."""
    conn = connect()
    try:
        f = report_counts(conn, place_id, "flicker")
        nz = report_counts(conn, place_id, "noise", hour=hour)
        tp = report_counts(conn, place_id, "temp", hour=hour)
    finally:
        conn.close()
    return {"place_id": place_id, **summary(f["yes"], f["no"]),
            "noise": {**nz, "count": sum(nz.values())}, "temp": {**tp, "count": sum(tp.values())}}


@router.get("/report/mine")
def my_reports(user_id: str):
    """열람 요청: 이 기기 ID로 남은 제보 목록 (보관 기간 90일 안의 것만 있음)."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT place_id, item, value, created_at FROM {TABLE} WHERE user_hash=%s "
                        f"ORDER BY created_at DESC", (_hash(user_id),))
            rows = cur.fetchall()
    finally:
        conn.close()
    return {"count": len(rows),
            "reports": [{"place_id": p, "item": i, "value": v, **({"flicker": v} if i == "flicker" else {}),
                         "reported_at_kst": (t + KST).isoformat()} for p, i, v, t in rows]}


@router.delete("/report/mine")
def delete_my_reports(user_id: str):
    """삭제 요청: 이 기기 ID의 제보 원본을 모두 지운다 (이미 집계표로 옮겨진 개수는 식별값이 없어 대상 아님)."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            n = cur.execute(f"DELETE FROM {TABLE} WHERE user_hash=%s", (_hash(user_id),))
        conn.commit()
    finally:
        conn.close()
    return {"deleted": n}


if __name__ == "__main__":
    import sys
    c = connect()
    try:
        if "--create" in sys.argv:
            ensure_tables(c)
            print(f"{TABLE}, {DAILY_TABLE} 표 준비 완료")
        elif "--purge" in sys.argv:
            print(f"{_utcnow():%Y-%m-%d %H:%M} UTC  {RETENTION_DAYS}일 지난 제보 {purge(c)}건 파기")
        else:
            print(CREATE_SQL)
    finally:
        c.close()
