-- 센서 없는 카페 목록 (정릉 일대, 카카오 로컬 CE7). id 는 'k' + 카카오 장소 id
CREATE TABLE IF NOT EXISTS places (
  id         VARCHAR(20)  NOT NULL PRIMARY KEY,
  name       VARCHAR(100) NOT NULL,
  lat        DOUBLE       NOT NULL,
  lng        DOUBLE       NOT NULL,
  source     VARCHAR(20)  NOT NULL DEFAULT 'kakao',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4;
