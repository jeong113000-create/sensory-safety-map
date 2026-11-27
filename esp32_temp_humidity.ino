/*
 * 감각안전지도 - 온습도 전용 ESP32 코드
 * ------------------------------------------------------------
 * 하는 일: DHT11로 온도(℃)와 습도(%)를 10초마다 재서
 *          와이파이로 수집 서버(POST /sensor)에 JSON으로 보낸다.
 *          (조도 센서 GL5537은 이 코드에서 뺐다.)
 *
 * 배선 (DHT11 3핀 모듈, 풀업 저항 기판에 내장)
 *   DHT11 VCC  -> ESP32 3.3V
 *   DHT11 GND  -> ESP32 GND
 *   DHT11 DATA -> ESP32 GPIO 4
 *
 * 필요한 라이브러리 (아두이노 IDE > 라이브러리 매니저)
 *   - "DHT sensor library" (Adafruit)  + 같이 설치되는 "Adafruit Unified Sensor"
 *   - 보드: esp32 by Espressif (2.x, 3.x 둘 다 됨)
 *
 * 시각은 보드가 보내지 않는다. 서버가 받은 시각(UTC)을 저장한다.
 * (보드 시계가 서로 달라서 3차 멘토링 권고로 서버 시각 하나로 통일)
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include <esp_task_wdt.h>

// ===== 1. 설정 (여기만 바꾸면 됨) =====================================
const char* WIFI_SSID     = "와이파이_이름";       // ← 직접 입력 (채팅·문서에 쓰지 말 것)
const char* WIFI_PASSWORD = "와이파이_비밀번호";   // ← 직접 입력
const char* SERVER_URL    = "http://서버주소:8000/sensor";
const char* SENSOR_ID     = "cafe-A";              // 카페 B 보드면 "cafe-B"

#define DHT_PIN   4          // DHT11 데이터선을 꽂은 핀
#define DHT_TYPE  DHT11

const unsigned long SEND_INTERVAL_MS = 10UL * 1000;            // 10초마다 측정·전송 (1시간 360건)

// ===== 2. 자동 재부팅 기준 (매장 직원이 관리할 수 없어서 보드가 스스로 복구) =====
const unsigned long WIFI_CONNECT_TIMEOUT_MS = 20UL * 1000;     // 와이파이 20초 안에 못 붙으면
const unsigned long NO_SERVER_OK_MS         = 5UL * 60 * 1000; // 서버 정상 응답이 5분 동안 없으면
const int           MAX_SEND_FAILS          = 30;              // 전송 30번 연속 실패하면
const unsigned long DHT_FAIL_MS             = 60UL * 1000;     // DHT11을 1분 동안 계속 못 읽으면
const unsigned long DAILY_REBOOT_MS         = 24UL * 60 * 60 * 1000; // 24시간마다 예방 재부팅
const int           WDT_TIMEOUT_S           = 60;              // 프로그램이 60초 멈추면 칩이 스스로 리셋

// ===== 3. 상태 기억용 변수 =============================================
DHT dht(DHT_PIN, DHT_TYPE);

unsigned long lastSendMs     = 0;   // 마지막으로 측정·전송한 시각
unsigned long lastServerOkMs = 0;   // 서버가 마지막으로 정상 응답한 시각
unsigned long lastDhtOkMs    = 0;   // DHT11을 마지막으로 제대로 읽은 시각
int sendFailCount            = 0;   // 연속 전송 실패 횟수

// 이유를 시리얼 모니터에 남기고 재부팅
void rebootWithReason(const char* reason) {
  Serial.print("[재부팅] ");
  Serial.println(reason);
  delay(1000);
  ESP.restart();
}

// 와이파이 연결. 20초 안에 안 되면 재부팅
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.print("와이파이 연결 중");
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);              // 절전 모드 끔 (연결이 끊기는 문제 줄이기)
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > WIFI_CONNECT_TIMEOUT_MS) {
      rebootWithReason("와이파이 20초 연결 실패");
    }
    esp_task_wdt_reset();
    delay(500);
    Serial.print(".");
  }
  Serial.print(" 연결됨, IP: ");
  Serial.println(WiFi.localIP());
}

// 60초 하드웨어 워치독 켜기 (보드 코어 버전에 따라 함수 모양이 다름)
void startWatchdog() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t cfg = {
    .timeout_ms = WDT_TIMEOUT_S * 1000,
    .idle_core_mask = 0,
    .trigger_panic = true
  };
  esp_task_wdt_deinit();             // 3.x는 기본 워치독이 켜져 있을 수 있어 새로 설정
  esp_task_wdt_init(&cfg);
#else
  esp_task_wdt_init(WDT_TIMEOUT_S, true);
#endif
  esp_task_wdt_add(NULL);            // 지금 돌고 있는 loop()를 감시 대상에 넣음
}

// 온습도를 서버로 보냄. 성공(HTTP 200번대)이면 true
bool sendToServer(float temperature, float humidity) {
  HTTPClient http;
  http.setTimeout(5000);             // 서버가 5초 안에 답 없으면 실패로 처리
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");

  // 보내는 JSON 예: {"sensor_id":"cafe-A","temperature":23.0,"humidity":41.0}
  String body = String("{\"sensor_id\":\"") + SENSOR_ID +
                "\",\"temperature\":" + String(temperature, 1) +
                ",\"humidity\":" + String(humidity, 1) + "}";

  int code = http.POST(body);
  http.end();

  Serial.printf("전송 %s -> 응답 %d\n", body.c_str(), code);
  return (code >= 200 && code < 300);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n감각안전지도 온습도 보드 시작");

  startWatchdog();
  dht.begin();
  connectWiFi();

  unsigned long now = millis();
  lastServerOkMs = now;              // 시작 직후 바로 재부팅되지 않게 기준 시각을 지금으로
  lastDhtOkMs    = now;
  lastSendMs     = now - SEND_INTERVAL_MS;  // 첫 측정은 바로
}

void loop() {
  esp_task_wdt_reset();              // "나 살아 있음" 신호 (60초 안 오면 칩 리셋)
  unsigned long now = millis();

  // 24시간마다 예방 재부팅
  if (now > DAILY_REBOOT_MS) rebootWithReason("24시간 예방 재부팅");

  // 와이파이가 끊겼으면 다시 연결 (20초 실패 시 재부팅)
  connectWiFi();

  // 10초가 지났을 때만 측정·전송
  if (now - lastSendMs < SEND_INTERVAL_MS) {
    delay(50);
    return;
  }
  lastSendMs = now;

  // --- 측정 ---
  float humidity    = dht.readHumidity();
  float temperature = dht.readTemperature();   // 섭씨

  if (isnan(humidity) || isnan(temperature)) {
    // 읽기 실패: 이번 회차는 보내지 않음 (빈 값을 서버에 넣지 않기)
    Serial.println("DHT11 읽기 실패");
    if (now - lastDhtOkMs > DHT_FAIL_MS) rebootWithReason("DHT11 1분 계속 읽기 실패");
  } else {
    lastDhtOkMs = now;

    // --- 전송 ---
    if (sendToServer(temperature, humidity)) {
      sendFailCount  = 0;
      lastServerOkMs = now;
    } else {
      sendFailCount++;
      if (sendFailCount >= MAX_SEND_FAILS) rebootWithReason("전송 30번 연속 실패");
    }
  }

  // 서버 정상 응답이 5분 동안 없으면 재부팅 (센서 실패로 못 보낸 경우도 포함)
  if (millis() - lastServerOkMs > NO_SERVER_OK_MS) rebootWithReason("서버 정상 응답 5분 없음");
}
