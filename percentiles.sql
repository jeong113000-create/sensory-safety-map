-- =====================================================================
-- 감각안전지도: 소음·조도 신호등 기준값 (카페별 50% / 80% 백분위)
-- RDS sensory_db 에서 실행 (MySQL 8 이상, 윈도 함수 사용)
--
-- 규칙
--  * sensor_data.created_at 은 UTC → +9시간 해서 KST 날짜/시각으로 묶음
--  * 1시간에 기대 개수 360개(10초 간격)의 70% = 252개 미만인 시간대는 제외
--  * 빈 값은 채우지 않음
--  * 백분위는 "시간 평균값"의 분포로 계산
--    (앱 신호등이 시간 단위 값에 색을 매기므로 같은 단위로 맞춤)
--  * 기본은 영업시간대만 (@h_from ~ @h_to 시, KST). 밤에 문 닫은 시간이
--    섞이면 조용·어두운 값이 많아져 기준이 낮아지고 낮 시간이 전부
--    노랑/빨강이 됨. 1) 진단 쿼리로 실제 영업시간 확인 후 값 조정.
-- =====================================================================

SET @h_from = 9;    -- 포함 (09:00~09:59 부터)
SET @h_to   = 22;   -- 포함 (22:00~22:59 까지)
SET @min_n  = 252;  -- 360 * 0.7


-- ---------------------------------------------------------------------
-- 1) 진단: 시간대(KST)별 평균 소음·조도. 카페 영업시간 감 잡기용
-- ---------------------------------------------------------------------
WITH hourly AS (
  SELECT sensor_id,
         DATE(DATE_ADD(created_at, INTERVAL 9 HOUR)) AS d,
         HOUR(DATE_ADD(created_at, INTERVAL 9 HOUR)) AS h,
         COUNT(sound) AS n_sound, AVG(sound) AS sound,
         COUNT(light) AS n_light, AVG(light) AS light
  FROM sensor_data
  GROUP BY sensor_id, d, h
)
SELECT sensor_id, h AS hour_kst,
       COUNT(*)                                        AS hours_total,
       ROUND(AVG(CASE WHEN n_sound >= @min_n THEN sound END), 1) AS avg_sound,
       ROUND(AVG(CASE WHEN n_light >= @min_n THEN light END), 1) AS avg_light
FROM hourly
GROUP BY sensor_id, h
ORDER BY sensor_id, h;


-- ---------------------------------------------------------------------
-- 2) 진단: 조도 4095(최대값 고정) 비율. 높으면 해당 기간 보정/제외 필요
-- ---------------------------------------------------------------------
SELECT sensor_id,
       COUNT(light)                                   AS n,
       SUM(light >= 4095)                             AS n_4095,
       ROUND(100 * SUM(light >= 4095) / COUNT(light), 1) AS pct_4095
FROM sensor_data
WHERE light IS NOT NULL
GROUP BY sensor_id;


-- ---------------------------------------------------------------------
-- 3) 본 쿼리: 카페별 50% / 80% 값  ← 이 결과를 app_api.py THRESHOLDS 에 넣음
--    (nearest-rank 방식: 정렬해서 ceil(p*N) 번째 값)
-- ---------------------------------------------------------------------
WITH raw AS (
  SELECT
    CASE WHEN sensor_id LIKE 'cafe-A%' THEN 'cafe-A'
         WHEN sensor_id LIKE 'cafe-B%' THEN 'cafe-B' END AS place_id,
    DATE(DATE_ADD(created_at, INTERVAL 9 HOUR)) AS d,
    HOUR(DATE_ADD(created_at, INTERVAL 9 HOUR)) AS h,
    sound, light
  FROM sensor_data
  WHERE sensor_id IN ('cafe-A', 'cafe-B', 'cafe-A-sound', 'cafe-B-sound')
),
hourly AS (
  SELECT place_id, d, h,
         COUNT(sound) AS n_sound, AVG(sound) AS sound,
         COUNT(light) AS n_light, AVG(light) AS light
  FROM raw
  WHERE h BETWEEN @h_from AND @h_to
  GROUP BY place_id, d, h
),
vals AS (
  SELECT place_id, 'sound' AS metric, sound AS v FROM hourly WHERE n_sound >= @min_n
  UNION ALL
  SELECT place_id, 'light',           light      FROM hourly WHERE n_light >= @min_n
),
ranked AS (
  SELECT place_id, metric, v,
         ROW_NUMBER() OVER (PARTITION BY place_id, metric ORDER BY v) AS rn,
         COUNT(*)     OVER (PARTITION BY place_id, metric)            AS cnt
  FROM vals
)
SELECT place_id, metric,
       MAX(cnt)                                              AS hours_used,
       ROUND(MIN(v), 1)                                      AS min_v,
       ROUND(MIN(CASE WHEN rn >= CEIL(0.5 * cnt) THEN v END), 1) AS p50,
       ROUND(MIN(CASE WHEN rn >= CEIL(0.8 * cnt) THEN v END), 1) AS p80,
       ROUND(MAX(v), 1)                                      AS max_v
FROM ranked
GROUP BY place_id, metric
ORDER BY place_id, metric;

-- 결과 예시 형태:
-- place_id | metric | hours_used | min_v | p50   | p80   | max_v
-- cafe-A   | light  | 210        | ...   | ...   | ...   | ...
-- cafe-A   | sound  | 95         | ...
-- cafe-B   | light  | ...
-- cafe-B   | sound  | ...
-- hours_used 가 너무 적으면(예: 50 미만) 기준이 불안정하니 팀에 공유할 때 같이 적기.
