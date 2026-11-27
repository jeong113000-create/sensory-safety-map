"""
센서 없는 '추정 카페' 하나를 카카오 장소 검색으로 찾아 등록 (2026-11-24, 할리스 국민대 성곡도서관점).

하는 일 (실행 1번):
1. 카카오 키워드 검색으로 장소를 찾음. 이름에 MUST 글자가 모두 들어간 곳이 정확히 1곳일 때만 진행, 아니면 후보만 출력하고 멈춤.
2. DB places 표에 id "kakao-<카카오 id>", 이름, 좌표를 넣음 (이미 있으면 이름·좌표만 갱신).
3. 카카오 좌표→주소로 동·주소를 구해 places_addr.json 에 그 카페만 추가 (다른 카페 주소는 그대로).
4. est_extra.json 에 "이 카페는 SRC 센서 카페 예측을 빌려 씀 + 영업시간"을 기록 (est_extra_ext.py 가 읽음).
키는 환경변수 KAKAO_REST_KEY 로만 받음 (파일에 저장 안 함).
"""
import json, math, os, sys, urllib.parse, urllib.request

QUERY = "할리스 국민대 성곡도서관"
MUST = ("할리스", "성곡")
SRC = "cafe-A"
OPEN, CLOSE, CLOSED_DAYS = 12, 18, [6]          # 일요일(6) 휴무
HOME = "/home/ubuntu"
KEY = os.environ.get("KAKAO_REST_KEY", "").strip()


def kakao(path, **q):
    req = urllib.request.Request(f"https://dapi.kakao.com{path}?{urllib.parse.urlencode(q)}",
                                 headers={"Authorization": f"KakaoAK {KEY}"})
    return json.loads(urllib.request.urlopen(req, timeout=8).read().decode())


def dist_m(a1, o1, a2, o2):
    p1, p2 = math.radians(a1), math.radians(a2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(o2 - o1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def main():
    if not KEY:
        sys.exit("KAKAO_REST_KEY 가 비어 있어요")
    docs = kakao("/v2/local/search/keyword.json", query=QUERY, size=15).get("documents", [])
    hit = [d for d in docs if all(m in d["place_name"] for m in MUST)]
    if len(hit) != 1:
        for d in docs:
            print("후보", d["id"], d["place_name"], d.get("road_address_name"), d["y"], d["x"])
        sys.exit(f"이름에 {MUST} 가 모두 들어간 곳이 {len(hit)}곳이라 멈췄어요. 아무것도 안 바꿨어요.")
    d = hit[0]
    pid, name, lat, lng = f"kakao-{d['id']}", d["place_name"], float(d["y"]), float(d["x"])
    print("찾음", pid, name, d.get("road_address_name"), lat, lng)

    sys.path.insert(0, HOME)
    import main as _m  # noqa: F401  (DB 설정 읽기)
    import app_api as A
    import pymysql
    for sid, sp in A.PLACES.items():
        print(f"거리 {sid} {sp['name']}: {dist_m(lat, lng, sp['lat'], sp['lng']):.0f} m")
    c = pymysql.connect(charset="utf8mb4", **A._DB)
    try:
        with c.cursor() as cur:
            cur.execute("INSERT INTO places (id, name, lat, lng, source) VALUES (%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE name=VALUES(name), lat=VALUES(lat), lng=VALUES(lng)",
                        (pid, name, lat, lng, "manual"))
        c.commit()
    finally:
        c.close()
    print("DB places 등록 OK")

    addr_path = os.path.join(HOME, "places_addr.json")
    try:
        with open(addr_path, encoding="utf-8") as f:
            addr = json.load(f)
    except Exception:
        addr = {}
    a = kakao("/v2/local/geo/coord2address.json", x=lng, y=lat).get("documents") or [{}]
    ra, ja = a[0].get("road_address") or {}, a[0].get("address") or {}
    addr[pid] = {"name": name, "address": ra.get("address_name") or ja.get("address_name"),
                 "dong": ja.get("region_3depth_name")}
    with open(addr_path, "w", encoding="utf-8") as f:
        json.dump(addr, f, ensure_ascii=False, indent=1)
    print("주소 OK", addr[pid]["dong"], addr[pid]["address"], "/ 주소 파일 전체", len(addr), "곳")

    extra_path = os.path.join(HOME, "est_extra.json")
    try:
        with open(extra_path, encoding="utf-8") as f:
            extra = json.load(f)
    except Exception:
        extra = {}
    extra[pid] = {"name": name, "src": SRC, "open": [OPEN, CLOSE], "closed_days": CLOSED_DAYS}
    with open(extra_path, "w", encoding="utf-8") as f:
        json.dump(extra, f, ensure_ascii=False, indent=1)
    print("설정 OK", extra_path, extra[pid])
    print("PLACE_ID", pid)


if __name__ == "__main__":
    main()
