#include <Arduino.h>
#include <Servo.h>

const int HAND_TRIG_PIN = 2;
const int HAND_ECHO_PIN = 3;
const int FILL_TRIG_PIN = 4;
const int FILL_ECHO_PIN = 5;
const int SERVO_PIN = 9;

const int LID_CLOSED_ANGLE = 90;
const int LID_OPEN_ANGLE = 0;
const int LID_STEP_DEGREES = 2;
const unsigned long LID_STEP_TIME_MS = 15;
const unsigned long LID_STAY_OPEN_MS = 3000;

const float HAND_OPEN_DISTANCE_CM = 15.0;
const unsigned long HAND_CHECK_TIME_MS = 100;

const float EMPTY_BIN_DISTANCE_CM = 30.0;
const float FULL_BIN_DISTANCE_CM = 5.0;
const unsigned long FILL_CHECK_TIME_MS = 2000;
const unsigned long LID_SETTLE_TIME_MS = 1000;

const unsigned long ECHO_TIMEOUT_US = 25000;
const unsigned long QUIET_TIME_MS = 30;
const unsigned long PRINT_TIME_MS = 1000;

Servo lidServo;

int currentAngle = LID_CLOSED_ANGLE;
bool lidShouldBeOpen = false;
bool lidIsClosed = true;

float handDistanceCm = -1;
float fillDistanceCm = -1;
int fillPercent = -1;

unsigned long lastHandSeenTime = 0;
unsigned long lidClosedTime = 0;
unsigned long lastHandCheckTime = 0;
unsigned long lastFillCheckTime = 0;
unsigned long lastPingTime = 0;
unsigned long lastStepTime = 0;
unsigned long lastPrintTime = 0;

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

void checkHand(unsigned long now) {
  handDistanceCm = readDistanceCm(HAND_TRIG_PIN, HAND_ECHO_PIN);
  bool handIsClose = (handDistanceCm > 0 && handDistanceCm < HAND_OPEN_DISTANCE_CM);

  if (handIsClose) {
    lastHandSeenTime = now;
    if (!lidShouldBeOpen) {
      lidShouldBeOpen = true;
      lidIsClosed = false;
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
  Serial.print("%) | Lid: ");
  Serial.println(lidIsClosed ? "closed" : "open");
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("Smart Trash Can - sensors + servo test");

  pinMode(HAND_TRIG_PIN, OUTPUT);
  pinMode(HAND_ECHO_PIN, INPUT);
  pinMode(FILL_TRIG_PIN, OUTPUT);
  pinMode(FILL_ECHO_PIN, INPUT);
  digitalWrite(HAND_TRIG_PIN, LOW);
  digitalWrite(FILL_TRIG_PIN, LOW);

  lidServo.attach(SERVO_PIN);
  lidServo.write(LID_CLOSED_ANGLE);
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

  if (now - lastPrintTime >= PRINT_TIME_MS) {
    lastPrintTime = now;
    printStatus();
  }
}