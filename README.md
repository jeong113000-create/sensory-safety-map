# 감각안전지도 (Sensory Safety Map)

카페 실내의 소음·온도·습도를 센서로 재고, 시간대별 감각 부담을 신호등 색으로 알려 주는 서비스입니다.
AI 상상 그라운드 9팀 "지도유지" (남유현 · 박건도 · 정지연 · 최지훈)

## 이 저장소에 있는 것

앱 API 서버 코드(FastAPI)와 외부 데이터 수집 스크립트입니다. 보고서 5.6절 "앱 API 서버"에 해당합니다.

```
server/
  app_api.py          앱 API 기본 구조 (/places, /indoor, /street, /now, /thresholds)
  sensory_score.py    항목별 점수와 색 기준 (온도·습도: Chiang 외 2001, 소음: Rindel)
  user_reports.py     원탭 제보 저장·집계·90일 파기 (/report, /report/summary, /report/mine)
  *_ext.py            기능 확장 파일 (아래 순서로 app_api.py 끝에서 import)
  sql/                표 정의와 기준값 계산 쿼리
  jobs/               수집·장소 등록 스크립트 (크론으로 실행)
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
9. est_fix_ext: 고장 센서값을 빌려 온 추정 습도 제외

demo_off_ext.py는 시연이 끝난 뒤 시연용 값을 끄기 위한 파일로, 평소에는 불러오지 않습니다.

## 들어 있지 않은 것

- **비밀값**: DB 주소·비밀번호와 API 키는 넣지 않았습니다. 코드는 서버의 main.py 또는 환경변수(RDS_HOST, RDS_USER, RDS_PASSWORD, RDS_DB, KAKAO_REST_KEY 등)에서 값을 읽습니다.
- **main.py**: 센서 수신(POST /sensor)과 DB 접속 설정이 들어 있어 올리지 않았습니다.
- **ESP32 펌웨어, 예측 학습 코드, 음성 에이전트 서버**: 각 담당자가 따로 추가합니다.

## 참고

- 시간 기준: sensor_data 표만 UTC, 나머지 표와 API 응답은 한국 시간(KST)입니다.
- app_api.py는 개발 중 사본이라 운영 서버 파일과 일부 다를 수 있습니다(운영 서버에서는 시연 장소 코드를 demo_ext.py로 분리해 씁니다).
