# 감각안전지도 (Sensory Safety Map)

카페 실내의 소음·온도·습도를 센서로 재고, 시간대별 감각 부담을 신호등 색으로 알려 주는 서비스입니다.
AI 상상 그라운드 9팀 "지도유지" (남유현 · 박건도 · 정지연 · 최지훈)

## 이 저장소에 있는 것

앱 API 서버 코드(FastAPI)와 외부 데이터 수집 스크립트입니다. 보고서 5.6절 "앱 API 서버"에 해당합니다.

```
app_api.py          앱 API 기본 구조 (/places, /indoor, /street, /now, /thresholds)
sensory_score.py    항목별 점수와 색 기준 (온도·습도: Chiang 외 2001, 소음: Rindel)
user_reports.py     원탭 제보 저장·집계·90일 파기 (/report, /report/summary, /report/mine)
*_ext.py            기능 확장 파일 (아래 순서로 app_api.py 끝에서 import)
*.sql               표 정의와 기준값 계산 쿼리 (places, predictions, percentiles)
fetch_weather_vilage.py, load_places.py, prune_places.py, fill_addr.py, add_est_cafe.py
                    수집·장소 등록 스크립트 (서버에서는 jobs 폴더에 두고 크론으로 실행)
esp32_temp_humidity.ino   온습도 보드(ESP32 + DHT11) 펌웨어. 10초마다 POST /sensor, 자동 재부팅·워치독
```

확장 파일은 서버의 app_api.py 맨 끝에서 아래 순서로 불러옵니다. 뒤에 불러온 파일이 앞의 규칙을 덮어씁니다.

1. places_ext: 장소 목록(places 표) 연결
2. score_ext: 감각부담 점수, 원탭 제보 API 연결
3. demo_ext: 시연용 장소 값, 데이터 없는 카페의 회색 응답
4. sens_ext: 가중 점수 연결(이후 hours_ext가 가장 나쁜 항목 색으로 다시 바꿈)
5. hours_ext: 영업시간 밖 회색, 항목별 색, 제보 기반 색
6. rindel_ext: 소음 색 경계(71 dB / 80 dB)
7. est_ext: 센서 없는 카페의 추정값(level_source "estimated")
8. humoff_ext: 고장 습도 센서 색 제외
9. est_fix_ext: 고장 센서값을 빌려 온 추정 습도 제외, /indoor level_source
10. seongsu_ext: 발표장(성수) 카페 영업시간·안내 문구
11. final_ext: 최종 시연 구성 (차담 = 포틀리에 실측 모델 "estimated", 카페쿠피 = 목데이터 모델 "demo", 나머지 센서 없는 카페는 원탭 제보만, 정릉동커피 예측 칸 "demo")
12. hours2_ext: 차담·카페쿠피 영업시간
13. nowcell_ext: 센서 카페 /indoor 지금 칸 실측, 모든 카페 /now level_source·indoor.source
14. est_extra_ext: 설정 파일(est_extra.json)로 센서 없는 카페를 추정 카페로 추가 (2026-11-24). 지금은 설정이 비어 있어 아무 일도 하지 않음(할리스 국민대성곡도서관점은 추정 대신 제보 전용으로 등록, add_est_cafe.py로 places 표에 추가)

**app_api.py를 읽을 때 주의:** app_api.py는 처음 만든 기본 구조라, 그 안의 `_level()`(소음·조도 백분위, 온습도 고정 범위), `_summary()`("쾌적해요" 같은 문장), `DEMO_PLACES`(cafe-001~004)는 초기 규칙입니다. 실제 응답에서는 위 확장 파일이 이 부분을 바꿉니다. 색은 score_ext·rindel_ext가 sensory_score.py(소음 71/80 dB, 온습도 Chiang 외 2001)로, 문장은 hours_ext가 "부담이 낮아요/보통이에요/높아요"로 바꾸고, 시연 카페는 final_ext가 끕니다. 지금 쓰는 기준은 서버의 GET /thresholds에서 확인할 수 있습니다.

demo_off_ext.py는 시연이 끝난 뒤 시연용 값을 끄기 위한 파일로, 평소에는 불러오지 않습니다.

## 들어 있지 않은 것

- **비밀값**: DB 주소·비밀번호와 API 키는 넣지 않았습니다. 코드는 서버의 main.py 또는 환경변수(RDS_HOST, RDS_USER, RDS_PASSWORD, RDS_DB, KAKAO_REST_KEY 등)에서 값을 읽습니다.
- **main.py**: 센서 수신(POST /sensor)과 DB 접속 설정이 들어 있어 올리지 않았습니다.
- **소음 보드 펌웨어, 예측 학습 코드, 음성 에이전트 서버**: 각 담당자가 따로 추가합니다. 온습도 보드 펌웨어는 esp32_temp_humidity.ino이고, 서버 주소와 와이파이 정보는 자리표시로 바꿔 두었습니다.

## 참고

- 시간 기준: sensor_data 표만 UTC, 나머지 표와 API 응답은 한국 시간(KST)입니다.
- app_api.py는 개발 중 사본이라 운영 서버 파일과 일부 다를 수 있습니다(운영 서버에서는 시연 장소 코드를 demo_ext.py로 분리해 씁니다).
