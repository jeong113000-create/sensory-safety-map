"""
/places 의 모든 장소 좌표 → 카카오 좌표→주소 API → /home/ubuntu/places_addr.json  (한 번만 실행)
키는 환경변수 KAKAO_REST_KEY 로만 받음 (파일에 저장하지 않음).

    python3 ~/fill_addr.py
결과 파일은 hours_ext.py 가 읽어서 /places 의 address, dong 으로 내보냄 (1분마다 다시 읽음).
"""
import json
import os
import sys
import time
import urllib.request

KEY = os.environ.get("KAKAO_REST_KEY", "").strip()
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "places_addr.json")


def addr(lat, lng):
    url = "https://dapi.kakao.com/v2/local/geo/coord2address.json?x=%s&y=%s" % (lng, lat)   # x=경도, y=위도
    req = urllib.request.Request(url, headers={"Authorization": "KakaoAK " + KEY})
    d = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
    docs = d.get("documents") or []
    if not docs:
        return "", ""
    a = docs[0].get("address") or {}
    r = docs[0].get("road_address") or {}
    return (r.get("address_name") or a.get("address_name") or ""), (a.get("region_3depth_name") or "")


def main():
    if not KEY:
        sys.exit("KAKAO_REST_KEY 가 비어 있어요")
    places = json.loads(urllib.request.urlopen("http://localhost:8000/places", timeout=30).read().decode())
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)          # 실패한 곳은 예전 주소를 그대로 둠
    except Exception:
        prev = {}
    out, fail = dict(prev), 0
    for i, p in enumerate(places):
        try:
            full, dong = addr(p["lat"], p["lng"])
        except Exception as e:
            fail += 1
            print("실패", p["id"], e, flush=True)
            if i == 2 and fail == 3:
                sys.exit("처음 3곳이 모두 실패해서 멈췄어요 (키 확인). 주소 파일은 그대로예요.")
            continue
        out[p["id"]] = {"name": p["name"], "address": full or None, "dong": dong or None}
        print(p["id"], p["name"], dong, full, flush=True)
        time.sleep(0.1)
    if fail == len(places):
        sys.exit("전부 실패해서 주소 파일을 바꾸지 않았어요.")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("완료:", len(places), "곳, 실패", fail, "→", OUT)

if __name__ == "__main__":
    main()
