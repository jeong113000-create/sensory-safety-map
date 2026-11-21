-- predictions 테이블 (변경안) : 건도 모델 결과용
-- 예전 안(level, noise_db, crowd)에서 바뀜:
--   * 카페 안 신호등은 센서 항목만 쓰므로 crowd 제거
--   * 모델은 값만 넣고, 색(level)은 API가 기준값으로 계산 → 기준 바꿔도 다시 안 넣어도 됨
--   * 시간은 KST, 값 단위는 sensor_data 와 같음(보정 전 값 그대로)
CREATE TABLE IF NOT EXISTS predictions (
  place_id      VARCHAR(20) NOT NULL,     -- 'cafe-A' / 'cafe-B'
  date          DATE        NOT NULL,     -- KST
  hour          TINYINT     NOT NULL,     -- 0~23, KST
  noise         FLOAT NULL,               -- sound 시간 평균 예측
  temp          FLOAT NULL,
  humidity      FLOAT NULL,
  light         FLOAT NULL,
  confidence    VARCHAR(10) NULL,         -- 'high' / 'low'
  model_version VARCHAR(30) NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (place_id, date, hour)
);

-- (선택) /now 가 최근 10분만 읽을 때 빨라지는 인덱스. 데이터가 많아져 /now 가 느리면 실행.
-- CREATE INDEX idx_sensor_time ON sensor_data (sensor_id, created_at);
