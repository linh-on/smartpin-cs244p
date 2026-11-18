#include <Arduino.h>
#include <Servo.h>
#include <WiFiS3.h>
#include <SHA256.h>
#include "secrets.h"

const int HAND_TRIG_PIN = 2;
const int HAND_ECHO_PIN = 3;
const int FILL_TRIG_PIN = 4;
const int FILL_ECHO_PIN = 5;
const int SERVO_PIN = 9;

const int LID_CLOSED_ANGLE = 0;
const int LID_OPEN_ANGLE = 90;
const int LID_STEP_DEGREES = 2;
const unsigned long LID_STEP_TIME_MS = 15;
const unsigned long LID_STAY_OPEN_MS = 3000;

const float HAND_OPEN_DISTANCE_CM = 10.0;
const unsigned long HAND_CHECK_TIME_MS = 100;

const float EMPTY_BIN_DISTANCE_CM = 15.5;
const float FULL_BIN_DISTANCE_CM = 4.0;
const unsigned long FILL_CHECK_TIME_MS = 2000;
const unsigned long LID_SETTLE_TIME_MS = 1000;

const unsigned long ECHO_TIMEOUT_US = 25000;
const unsigned long QUIET_TIME_MS = 30;
const unsigned long PRINT_TIME_MS = 1000;

const char DEVICE_ID[] = "bin-1";
const unsigned long SEND_TIME_MS = 10000;
const unsigned long SEND_RETRY_AFTER_FAIL_MS = 60000;
const unsigned long WIFI_RETRY_TIME_MS = 30000;
const unsigned long SERVER_TIMEOUT_MS = 2000;
const int MAX_LID_OPENS_PER_MESSAGE = 5;
const int DEFAULT_FULL_LEVEL_PERCENT = 80;

Servo lidServo;

int currentAngle = LID_CLOSED_ANGLE;
bool lidShouldBeOpen = false;
bool lidIsClosed = true;

float handDistanceCm = -1;
float fillDistanceCm = -1;
int fillPercent = -1;

int fullLevelPercent = DEFAULT_FULL_LEVEL_PERCENT;
bool binIsFull = false;
int lidOpensToSend = 0;
unsigned long messageNumber = 0;
String bootId;
bool wifiWasConnected = false;
bool lastSendWorked = false;

unsigned long lastHandSeenTime = 0;
unsigned long lidClosedTime = 0;
unsigned long lastHandCheckTime = 0;
unsigned long lastFillCheckTime = 0;
unsigned long lastPingTime = 0;
unsigned long lastStepTime = 0;
unsigned long lastPrintTime = 0;
unsigned long lastSendTime = 0;
unsigned long nextSendWaitMs = SEND_TIME_MS;
unsigned long lastWifiTryTime = 0;

float readDistanceCm(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  unsigned long echoTimeUs = pulseIn(echoPin, HIGH, ECHO_TIMEOUT_US);
  lastPingTime = millis();
  if (echoTimeUs == 0) return -1;

  return echoTimeUs * 0.0343 / 2.0;
}

int distanceToPercent(float distanceCm) {
  float percent = (EMPTY_BIN_DISTANCE_CM - distanceCm) * 100.0 /
                  (EMPTY_BIN_DISTANCE_CM - FULL_BIN_DISTANCE_CM);
  return constrain((int)percent, 0, 100);
}

void updateBinFull() {
  if (fillPercent < 0) return;
  bool fullNow = (fillPercent >= fullLevelPercent);
  if (fullNow == binIsFull) return;
  binIsFull = fullNow;
  Serial.println(binIsFull ? ">>> BIN FULL" : ">>> Bin no longer full");
}

void checkHand(unsigned long now) {
  handDistanceCm = readDistanceCm(HAND_TRIG_PIN, HAND_ECHO_PIN);
  bool handIsClose = (handDistanceCm > 0 && handDistanceCm < HAND_OPEN_DISTANCE_CM);

  if (handIsClose) {
    lastHandSeenTime = now;
    if (!lidShouldBeOpen) {
      lidShouldBeOpen = true;
      lidIsClosed = false;
      lidOpensToSend++;
      Serial.println(">>> Hand detected, opening lid");
    }
  } else if (lidShouldBeOpen && now - lastHandSeenTime >= LID_STAY_OPEN_MS) {
    lidShouldBeOpen = false;
    Serial.println(">>> No hand, closing lid");
  }
}

void checkFill(unsigned long now) {
  fillDistanceCm = readDistanceCm(FILL_TRIG_PIN, FILL_ECHO_PIN);
  if (fillDistanceCm > 0) {
    fillPercent = distanceToPercent(fillDistanceCm);
    updateBinFull();
  }
}

void moveLid(unsigned long now) {
  if (now - lastStepTime < LID_STEP_TIME_MS) return;
  lastStepTime = now;

  int targetAngle = lidShouldBeOpen ? LID_OPEN_ANGLE : LID_CLOSED_ANGLE;
  if (currentAngle == targetAngle) return;

  if (currentAngle < targetAngle) {
    currentAngle = min(currentAngle + LID_STEP_DEGREES, targetAngle);
  } else {
    currentAngle = max(currentAngle - LID_STEP_DEGREES, targetAngle);
  }
  lidServo.write(currentAngle);

  if (!lidShouldBeOpen && currentAngle == LID_CLOSED_ANGLE) {
    lidIsClosed = true;
    lidClosedTime = now;
    Serial.println(">>> Lid closed");
  }
}

void startWifi() {
  Serial.print("Wi-Fi: connecting to ");
  Serial.println(WIFI_NAME);
  WiFi.begin(WIFI_NAME, WIFI_PASSWORD);
  lastWifiTryTime = millis();
}

void maintainWifi(unsigned long now) {
  bool connected = (WiFi.status() == WL_CONNECTED);
  if (connected && !wifiWasConnected) {
    Serial.print("Wi-Fi: connected, board IP ");
    Serial.println(WiFi.localIP());
  }
  if (!connected && wifiWasConnected) {
    Serial.println("Wi-Fi: connection lost");
  }
  wifiWasConnected = connected;

  if (!connected && lidIsClosed && now - lastWifiTryTime >= WIFI_RETRY_TIME_MS) {
    startWifi();
  }
}

String buildMessage(int lidOpens) {
  String items = "{\"type\":\"reading\",\"seq\":";
  items += messageNumber++;
  if (handDistanceCm > 0) {
    items += ",\"hand_cm\":";
    items += String(handDistanceCm, 1);
  }
  if (fillDistanceCm > 0) {
    items += ",\"fill_cm\":";
    items += String(fillDistanceCm, 1);
  }
  if (fillPercent >= 0) {
    items += ",\"fill_percent\":";
    items += fillPercent;
  }
  items += ",\"lid_open\":";
  items += lidIsClosed ? "false" : "true";
  items += ",\"bin_full\":";
  items += binIsFull ? "true" : "false";
  items += ",\"wifi_rssi\":";
  items += WiFi.RSSI();
  items += ",\"uptime_s\":";
  items += millis() / 1000;
  items += "}";

  for (int i = 0; i < lidOpens; i++) {
    items += ",{\"type\":\"event\",\"seq\":";
    items += messageNumber++;
    items += ",\"name\":\"lid_open\",\"detail\":\"hand\"}";
  }

  String message = "{\"device_id\":\"";
  message += DEVICE_ID;
  message += "\",\"boot_id\":\"";
  message += bootId;
  message += "\",\"full_level_percent\":";
  message += fullLevelPercent;
  message += ",\"empty_distance_cm\":";
  message += String(EMPTY_BIN_DISTANCE_CM, 1);
  message += ",\"items\":[";
  message += items;
  message += "]}";
  return message;
}

int findNumberAfter(const String& text, const String& key) {
  int start = text.indexOf(key);
  if (start < 0) return -1;
  start += key.length();
  while (start < (int)text.length() && text[start] == ' ') start++;
  int end = start;
  while (end < (int)text.length() && isDigit(text[end])) end++;
  if (end == start) return -1;
  return text.substring(start, end).toInt();
}

// HMAC-SHA256 of the exact bytes we send, as 64 hex characters.
// The server recomputes it with the same key to check who sent the report
// and that nothing was changed on the way.
String signMessage(const String& message) {
  const size_t keyLength = strlen(DEVICE_HMAC_KEY);
  uint8_t digest[32];
  SHA256 sha;
  sha.resetHMAC(DEVICE_HMAC_KEY, keyLength);
  sha.update(message.c_str(), message.length());
  sha.finalizeHMAC(DEVICE_HMAC_KEY, keyLength, digest, sizeof(digest));

  const char hexDigits[] = "0123456789abcdef";
  String hex;
  hex.reserve(64);
  for (uint8_t b : digest) {
    hex += hexDigits[b >> 4];
    hex += hexDigits[b & 0x0F];
  }
  return hex;
}

bool sendReport() {
  int lidOpens = min(lidOpensToSend, MAX_LID_OPENS_PER_MESSAGE);
  String message = buildMessage(lidOpens);
  String signature = signMessage(message);

  WiFiClient client;
  client.setConnectionTimeout(SERVER_TIMEOUT_MS);
  if (!client.connect(SERVER_ADDRESS, SERVER_PORT)) {
    Serial.println("Server: could not connect (is app.py running? same hotspot? firewall?)");
    return false;
  }

  client.print("POST /api/device/report HTTP/1.1\r\n");
  client.print("Host: ");
  client.print(SERVER_ADDRESS);
  client.print("\r\n");
  client.print("Content-Type: application/json\r\n");
  client.print("X-Signature: ");
  client.print(signature);
  client.print("\r\n");
  client.print("Content-Length: ");
  client.print(message.length());
  client.print("\r\n");
  client.print("Connection: close\r\n\r\n");
  client.print(message);

  String reply = "";
  unsigned long start = millis();
  while ((client.connected() || client.available()) && millis() - start < SERVER_TIMEOUT_MS) {
    while (client.available()) {
      char c = client.read();
      if (reply.length() < 600) reply += c;
    }
  }
  client.stop();

  if (reply.indexOf(" 200 ") < 0) {
    Serial.print("Server: unexpected reply: ");
    Serial.println(reply.substring(0, reply.indexOf('\n')));
    return false;
  }

  lidOpensToSend -= lidOpens;
  int newLevel = findNumberAfter(reply, "\"full_level_percent\":");
  if (newLevel >= 10 && newLevel <= 100 && newLevel != fullLevelPercent) {
    fullLevelPercent = newLevel;
    Serial.print(">>> New full level from website: ");
    Serial.print(fullLevelPercent);
    Serial.println("%");
    updateBinFull();
  }
  return true;
}

void printStatus() {
  Serial.print("Hand: ");
  if (handDistanceCm < 0) Serial.print("--");
  else Serial.print(handDistanceCm, 1);
  Serial.print(" cm | Fill: ");
  if (fillDistanceCm < 0) Serial.print("--");
  else Serial.print(fillDistanceCm, 1);
  Serial.print(" cm (");
  if (fillPercent < 0) Serial.print("--");
  else Serial.print(fillPercent);
  Serial.print("% of ");
  Serial.print(fullLevelPercent);
  Serial.print("%) | Lid: ");
  Serial.print(lidIsClosed ? "closed" : "open");
  Serial.print(" | WiFi: ");
  Serial.print(WiFi.status() == WL_CONNECTED ? "ok" : "--");
  Serial.print(" | Server: ");
  Serial.println(lastSendWorked ? "ok" : "--");
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("Smart Trash Can - sensors + servo + website");

  pinMode(HAND_TRIG_PIN, OUTPUT);
  pinMode(HAND_ECHO_PIN, INPUT);
  pinMode(FILL_TRIG_PIN, OUTPUT);
  pinMode(FILL_ECHO_PIN, INPUT);
  digitalWrite(HAND_TRIG_PIN, LOW);
  digitalWrite(FILL_TRIG_PIN, LOW);

  lidServo.attach(SERVO_PIN, 500, 2400);
  lidServo.write(LID_CLOSED_ANGLE);

  startWifi();
  randomSeed(micros() + analogRead(A0));
  bootId = "r4-";
  bootId += String(random(0x7FFFFFFF), HEX);

  lidClosedTime = millis();
}

void loop() {
  unsigned long now = millis();
  bool sensorsAreQuiet = (now - lastPingTime >= QUIET_TIME_MS);

  if (sensorsAreQuiet && now - lastHandCheckTime >= HAND_CHECK_TIME_MS) {
    lastHandCheckTime = now;
    checkHand(now);
  } else if (sensorsAreQuiet && lidIsClosed &&
             now - lidClosedTime >= LID_SETTLE_TIME_MS &&
             now - lastFillCheckTime >= FILL_CHECK_TIME_MS) {
    lastFillCheckTime = now;
    checkFill(now);
  }

  moveLid(now);
  maintainWifi(now);

  // Sending can block for several seconds when the server is unreachable,
  // so only send while the lid is fully closed and staying closed,
  // and wait longer before retrying after a failure.
  bool lidIsResting = lidIsClosed && !lidShouldBeOpen;
  if (lidIsResting && now - lastSendTime >= nextSendWaitMs && WiFi.status() == WL_CONNECTED) {
    lastSendWorked = sendReport();
    lastSendTime = millis();
    nextSendWaitMs = lastSendWorked ? SEND_TIME_MS : SEND_RETRY_AFTER_FAIL_MS;
  }

  if (now - lastPrintTime >= PRINT_TIME_MS) {
    lastPrintTime = now;
    printStatus();
  }
}