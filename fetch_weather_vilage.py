"""
기상청 단기예보(getVilageFcst) 수집 → weather_vilage_fcst
  - 앞으로 약 3일, 1시간 단위 (기온, 습도, 1시간 강수량, 강수확률, 강수형태, 하늘상태)
  - DB 설정은 fetch_weather_live.py 것을 그대로 가져다 씀 (여기엔 비번/키 없음)
  - ~/jobs/datago_key.txt 가 있으면 공공데이터포털(data.go.kr) 키로 요청 (Decoding 키 한 줄)
    없으면 기상청 API허브(fetch_weather_live.py 의 키)로 요청
  - 발표: 02,05,08,11,14,17,20,23시 (+약 10분 후 조회 가능)

사용:
  python3 ~/jobs/fetch_weather_vilage.py --dry-run   # DB에 안 넣고 결과만 출력
  크론: 15 2-23/3 * * *
"""
import argparse
import os
import sys
import time

import requests
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_weather_live import (get, kst_now, FCST_URL,  # noqa: E402
                                RDS_HOST, RDS_USER, RDS_PASSWORD, RDS_DB)

VILAGE_URL = FCST_URL.replace("getUltraSrtFcst", "getVilageFcst")
DATAGO_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
DATAGO_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datago_key.txt")
NX, NY = 61, 126
BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)


def latest_base(now):
    """지금 조회 가능한 가장 최근 발표시각 (발표 후 10분 여유)."""
    t = now - timedelta(minutes=10)
    for d in (0, 1):
        day = (t - timedelta(days=d)).date()
        for h in reversed(BASE_HOURS):
            cand = datetime(day.year, day.month, day.day, h)
            if cand <= t:
                return cand
    raise RuntimeError("발표시각 계산 실패")


def rain_mm(s):
    """PCP 문자열 → mm (강수없음 0, '1mm 미만' 0.5, '30.0~50.0mm' 30, '50.0mm 이상' 50)."""
    if s is None:
        return None
    s = str(s).strip()
    if s in ("강수없음", "", "-"):
        return 0.0
    if "미만" in s:
        return 0.5
    s = s.replace("mm", "").replace("이상", "").strip()
    if "~" in s:
        s = s.split("~")[0]
    try:
        return float(s)
    except ValueError:
        return None


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def get_datago(params):
    key = open(DATAGO_KEY_FILE).read().strip()
    for attempt in range(3):
        try:
            r = requests.get(DATAGO_URL, params={**params, "serviceKey": key}, timeout=20)
            try:
                return r.json()
            except ValueError:   # 키 오류 등은 XML로 옴
                raise RuntimeError(f"API 응답 이상 (키 확인): {r.text[:300]}")
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  재시도 {attempt + 1}: {e}")
            time.sleep(3)


def fetch(verbose=False):
    base = latest_base(kst_now())
    params = {"pageNo": 1, "numOfRows": 1000, "dataType": "JSON",
              "base_date": base.strftime("%Y%m%d"), "base_time": base.strftime("%H%M"),
              "nx": NX, "ny": NY}
    if os.path.exists(DATAGO_KEY_FILE):
        data = get_datago(params)
    else:
        data = get(VILAGE_URL, params, as_json=True)
    if "response" not in data:
        raise RuntimeError(f"API 응답 이상 (신청/권한 확인): {str(data)[:300]}")
    header = data["response"].get("header", {})
    if header.get("resultCode") not in ("00", None):
        raise RuntimeError(f"API 오류: {header}")
    items = data["response"]["body"]["items"]["item"]

    by_time = {}
    for it in items:
        ft = datetime.strptime(it["fcstDate"] + it["fcstTime"], "%Y%m%d%H%M")
        by_time.setdefault(ft, {})[it["category"]] = it["fcstValue"]

    rows = []
    for ft in sorted(by_time):
        v = by_time[ft]
        rows.append((ft, NX, NY, base, num(v.get("TMP")), num(v.get("REH")),
                     rain_mm(v.get("PCP")), num(v.get("POP")),
                     num(v.get("PTY")), num(v.get("SKY"))))
    if verbose:
        print(f"  발표 {base:%m-%d %H:%M}, {len(rows)}시간치 "
              f"({rows[0][0]:%m-%d %H시} ~ {rows[-1][0]:%m-%d %H시})" if rows else "  0건")
    return rows


def save(rows):
    import pymysql
    conn = pymysql.connect(host=RDS_HOST, user=RDS_USER, password=RDS_PASSWORD,
                           database=RDS_DB, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS weather_vilage_fcst (
                fcst_time DATETIME NOT NULL, nx INT NOT NULL, ny INT NOT NULL,
                base_time DATETIME, temp FLOAT, humidity FLOAT, rain FLOAT,
                pop TINYINT, pty TINYINT, sky TINYINT,
                PRIMARY KEY (fcst_time, nx, ny))""")
            cur.executemany("""INSERT INTO weather_vilage_fcst VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE base_time=VALUES(base_time), temp=VALUES(temp),
                humidity=VALUES(humidity), rain=VALUES(rain), pop=VALUES(pop),
                pty=VALUES(pty), sky=VALUES(sky)""", rows)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rows = fetch(verbose=True)
    print(f"[{kst_now():%m-%d %H:%M}] 단기예보 {len(rows)}시간치")
    if args.dry_run:
        for r in rows[:6]:
            print("   ", r)
    else:
        save(rows)
        print("  저장 완료")
