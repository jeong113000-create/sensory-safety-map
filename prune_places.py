"""
places 테이블에서 센서 카페(포틀리에·정릉동커피) 중 가까운 쪽 기준 반경 밖 카페 삭제.
cafe-A, cafe-B 는 이 테이블에 없고 app_api.py 코드 안에 있으니 영향 없음.

    python3 ~/jobs/prune_places.py --dry-run      # 남을/지울 개수만 출력 (DB 안 바꿈)
    python3 ~/jobs/prune_places.py                # 백업 테이블 places_backup_1113 만들고 삭제

되돌리기: python3 ~/jobs/prune_places.py --restore
"""

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RADIUS_M = 1000
POTELIER = (37.6089377, 127.0047461)
JN_SERVER = (37.6087137891644, 127.007122998329)   # 지금 /places 에 나가는 정릉동커피 좌표
JN_USER = (37.6057138, 127.0071230)                # 11/13 메시지에 적힌 정릉동커피 좌표
BACKUP = "places_backup_1113"


def dist_m(a1, o1, a2, o2):
    p1, p2 = math.radians(a1), math.radians(a2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(o2 - o1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def keep_ids(rows, centers):
    return {r[0] for r in rows
            if min(dist_m(r[1], r[2], a, o) for a, o in centers) <= RADIUS_M}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--use", choices=["server", "user"], default="server",
                    help="정릉동커피 좌표: server(지금 /places 값) / user(메시지 값)")
    a = ap.parse_args()

    import pymysql
    from fetch_weather_live import RDS_HOST, RDS_USER, RDS_PASSWORD, RDS_DB
    conn = pymysql.connect(host=RDS_HOST, user=RDS_USER, password=RDS_PASSWORD,
                           database=RDS_DB, charset="utf8mb4", connect_timeout=10)
    try:
        with conn.cursor() as cur:
            if a.restore:
                cur.execute(f"INSERT IGNORE INTO places SELECT * FROM {BACKUP}")
                conn.commit()
                cur.execute("SELECT COUNT(*) FROM places")
                print("복구 완료. places 전체", cur.fetchone()[0], "곳")
                return

            cur.execute("SELECT id, lat, lng FROM places")
            rows = [(r[0], float(r[1]), float(r[2])) for r in cur.fetchall()]
            print(f"지금 places 테이블: {len(rows)}곳 (cafe-A, cafe-B 는 별도라 항상 남음)")
            for label, jn in (("server", JN_SERVER), ("user", JN_USER)):
                k = keep_ids(rows, [POTELIER, jn])
                print(f"  정릉동커피 좌표 {label} {jn}: 남음 {len(k)}곳 / 지움 {len(rows) - len(k)}곳")
            if a.dry_run:
                return

            jn = JN_SERVER if a.use == "server" else JN_USER
            keep = keep_ids(rows, [POTELIER, jn])
            drop = [r[0] for r in rows if r[0] not in keep]
            cur.execute(f"DROP TABLE IF EXISTS {BACKUP}")
            cur.execute(f"CREATE TABLE {BACKUP} AS SELECT * FROM places")
            cur.execute(f"SELECT COUNT(*) FROM {BACKUP}")
            print("백업 완료:", BACKUP, cur.fetchone()[0], "곳")
            cur.executemany("DELETE FROM places WHERE id=%s", [(i,) for i in drop])
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM places")
            print(f"삭제 완료 (좌표 {a.use}). places 전체 {cur.fetchone()[0]}곳")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
