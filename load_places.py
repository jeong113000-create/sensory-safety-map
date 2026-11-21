"""
카페 목록 JSON -> RDS places 테이블.  ~/jobs 에 두고 실행 (DB 설정은 fetch_weather_live.py 것을 씀).

    python3 ~/jobs/load_places.py ~/cafes.json --dry-run   # 몇 곳 들어갈지만 출력
    python3 ~/jobs/load_places.py ~/cafes.json             # 테이블 만들고 넣기 (같은 id 는 덮어씀)
    python3 ~/jobs/load_places.py ~/cafes.json --replace   # 기존 목록 지우고 새로 넣기

JSON 은 리스트. 항목 형식은 둘 다 됨:
    {"id": "...", "name": "...", "lat": 37.6, "lng": 127.0}
    카카오 응답 그대로 {"id": "...", "place_name": "...", "y": "37.6", "x": "127.0"}
센서 카페(포틀리에·정릉동커피)와 같은 곳은 자동으로 뺌 (이름이 겹치고 50m 이내).
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SENSOR_CAFES = [("포틀리에", 37.6089377144737, 127.004746150444),
                ("정릉동커피", 37.6087137891644, 127.007122998329)]

DDL = """
CREATE TABLE IF NOT EXISTS places (
  id         VARCHAR(20)  NOT NULL PRIMARY KEY,
  name       VARCHAR(100) NOT NULL,
  lat        DOUBLE       NOT NULL,
  lng        DOUBLE       NOT NULL,
  source     VARCHAR(20)  NOT NULL DEFAULT 'kakao',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4
"""


def dist_m(a1, o1, a2, o2):
    p1, p2 = math.radians(a1), math.radians(a2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(o2 - o1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def norm(s):
    return "".join(s.split()).lower()


def parse(items):
    out, seen = [], set()
    for it in items:
        raw_id = str(it.get("id", "")).strip()
        name = (it.get("name") or it.get("place_name") or "").strip()
        lat = it.get("lat", it.get("y"))
        lng = it.get("lng", it.get("x"))
        if not raw_id or not name or lat is None or lng is None:
            continue
        pid = raw_id if raw_id.startswith("k") else "k" + raw_id
        if pid in seen or len(pid) > 20:
            continue
        lat, lng = float(lat), float(lng)
        if any(dist_m(lat, lng, a, o) < 50 and (norm(n) in norm(name) or norm(name) in norm(n))
               for n, a, o in SENSOR_CAFES):
            print("센서 카페와 같은 곳이라 뺌:", name)
            continue
        seen.add(pid)
        out.append((pid, name[:100], lat, lng))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replace", action="store_true")
    a = ap.parse_args()

    with open(os.path.expanduser(a.json_path), encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):   # {"documents": [...]} / {"places": [...]} 같은 형식도 받음
        data = next((v for v in data.values() if isinstance(v, list)), [])
    rows = parse(data)
    print(f"읽은 항목 {len(data)}개 -> 넣을 카페 {len(rows)}곳")
    for r in rows[:3]:
        print("  예:", r)
    if a.dry_run:
        return

    import pymysql
    from fetch_weather_live import RDS_HOST, RDS_USER, RDS_PASSWORD, RDS_DB
    conn = pymysql.connect(host=RDS_HOST, user=RDS_USER, password=RDS_PASSWORD,
                           database=RDS_DB, charset="utf8mb4", connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
            if a.replace:
                cur.execute("DELETE FROM places")
            cur.executemany(
                """INSERT INTO places (id, name, lat, lng) VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE name=VALUES(name), lat=VALUES(lat), lng=VALUES(lng)""",
                rows)
            cur.execute("SELECT COUNT(*) FROM places")
            total = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    print(f"완료. places 테이블 전체 {total}곳")


if __name__ == "__main__":
    main()
