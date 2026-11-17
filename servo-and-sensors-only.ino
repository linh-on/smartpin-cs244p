#include <Servo.h>
#include <EEPROM.h>

const int HAND_SENSOR_TRIG_PIN = 2;
const int HAND_SENSOR_ECHO_PIN = 3;
const int FILL_SENSOR_TRIG_PIN = 4;
const int FILL_SENSOR_ECHO_PIN = 5;
const int SERVO_PIN = 9;

const float HAND_OPEN_DISTANCE_CM = 10.0;
const int HAND_READINGS_NEEDED = 3;
const unsigned long KEEP_OPEN_MS = 3000;
const int LID_CLOSED_ANGLE = 0;
const int LID_OPEN_ANGLE = 90;
const int OPEN_STEP_DEGREES = 3;
const int CLOSE_STEP_DEGREES = 1;
const unsigned long SERVO_STEP_MS = 15;
const unsigned long CHECK_HAND_EVERY_MS = 100;
const unsigned long PRINT_HAND_EVERY_MS = 1000;

const unsigned long CHECK_FILL_EVERY_MS = 2000;
const unsigned long LID_SETTLE_MS = 1500;
const int FILL_SAMPLES = 5;
const float FULL_DISTANCE_CM = 4.0;
const int FULL_LEVEL_PERCENT = 80;
const float DEFAULT_EMPTY_DISTANCE_CM = 12.0;
const int SAVED_EMPTY_DISTANCE_ADDRESS = 0;
const float LONGEST_VALID_DISTANCE_CM = 400.0;

const unsigned long SENSOR_TIMEOUT_US = 25000;
const unsigned long SENSOR_GAP_MS = 60;

Servo lidServo;
int currentAngle = LID_CLOSED_ANGLE;
bool lidShouldBeOpen = false;
bool lidIsClosed = true;
int closeReadingsInARow = 0;
unsigned long lastHandSeenTime = 0;
unsigned long lastHandCheckTime = 0;
unsigned long lastHandPrintTime = 0;
unsigned long lastFillCheckTime = 0;
unsigned long lastServoStepTime = 0;
unsigned long lidClosedTime = 0;
unsigned long lastPingTime = 0;
float emptyDistanceCm = DEFAULT_EMPTY_DISTANCE_CM;

float readDistanceCm(int trigPin, int echoPin) {
  while (millis() - lastPingTime < SENSOR_GAP_MS) {
    delay(1);
  }
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);
  unsigned long echoTimeUs = pulseIn(echoPin, HIGH, SENSOR_TIMEOUT_US);
  lastPingTime = millis();
  if (echoTimeUs == 0) {
    return -1;
  }
  return echoTimeUs / 58.0;
}

float readFillDistanceCm() {
  float goodReadings[FILL_SAMPLES];
  int goodCount = 0;
  for (int i = 0; i < FILL_SAMPLES; i++) {
    float distanceCm = readDistanceCm(FILL_SENSOR_TRIG_PIN, FILL_SENSOR_ECHO_PIN);
    if (distanceCm > 0) {
      goodReadings[goodCount] = distanceCm;
      goodCount++;
    }
  }
  if (goodCount < 3) {
    return -1;
  }
  for (int i = 0; i < goodCount - 1; i++) {
    for (int j = i + 1; j < goodCount; j++) {
      if (goodReadings[j] < goodReadings[i]) {
        float temp = goodReadings[i];
        goodReadings[i] = goodReadings[j];
        goodReadings[j] = temp;
      }
    }
  }
  return goodReadings[goodCount / 2];
}

float fillPercentFromDistance(float distanceCm) {
  float usableDepthCm = emptyDistanceCm - FULL_DISTANCE_CM;
  if (usableDepthCm < 1) {
    return -1;
  }
  float percent = (emptyDistanceCm - distanceCm) / usableDepthCm * 100.0;
  if (percent < 0) percent = 0;
  if (percent > 100) percent = 100;
  return percent;
}

void checkHand(unsigned long now) {
  if (now - lastHandCheckTime < CHECK_HAND_EVERY_MS) return;
  lastHandCheckTime = now;

  float distanceCm = readDistanceCm(HAND_SENSOR_TRIG_PIN, HAND_SENSOR_ECHO_PIN);

  if (now - lastHandPrintTime >= PRINT_HAND_EVERY_MS) {
    lastHandPrintTime = now;
    Serial.print("Hand: ");
    if (distanceCm < 0) {
      Serial.println("no echo");
    } else {
      Serial.print(distanceCm, 1);
      Serial.println(" cm");
    }
  }

  if (distanceCm > 0 && distanceCm < HAND_OPEN_DISTANCE_CM) {
    closeReadingsInARow++;
  } else {
    closeReadingsInARow = 0;
  }

  if (closeReadingsInARow >= HAND_READINGS_NEEDED) {
    lastHandSeenTime = now;
    if (!lidShouldBeOpen) {
      lidShouldBeOpen = true;
      lidIsClosed = false;
      Serial.print(">>> Hand at ");
      Serial.print(distanceCm, 1);
      Serial.println(" cm: OPENING");
    }
  }

  if (lidShouldBeOpen && now - lastHandSeenTime >= KEEP_OPEN_MS) {
    lidShouldBeOpen = false;
    Serial.println(">>> Hand gone: CLOSING");
  }
}

void moveServo(unsigned long now) {
  if (now - lastServoStepTime < SERVO_STEP_MS) return;
  lastServoStepTime = now;

  int targetAngle = lidShouldBeOpen ? LID_OPEN_ANGLE : LID_CLOSED_ANGLE;
  if (currentAngle < targetAngle) {
    currentAngle = currentAngle + OPEN_STEP_DEGREES;
    if (currentAngle > targetAngle) currentAngle = targetAngle;
    lidServo.write(currentAngle);
  } else if (currentAngle > targetAngle) {
    currentAngle = currentAngle - CLOSE_STEP_DEGREES;
    if (currentAngle < targetAngle) currentAngle = targetAngle;
    lidServo.write(currentAngle);
    if (currentAngle == LID_CLOSED_ANGLE) {
      lidIsClosed = true;
      lidClosedTime = now;
      Serial.println(">>> Lid closed");
    }
  }
}

void checkFill(unsigned long now) {
  if (now - lastFillCheckTime < CHECK_FILL_EVERY_MS) return;
  if (!lidIsClosed || now - lidClosedTime < LID_SETTLE_MS) return;
  lastFillCheckTime = now;

  float distanceCm = readFillDistanceCm();
  if (distanceCm < 0) {
    Serial.println("Fill: no good reading (check the fill sensor wiring)");
    return;
  }
  float fillPercent = fillPercentFromDistance(distanceCm);
  Serial.print("Fill: ");
  Serial.print(distanceCm, 1);
  Serial.print(" cm to trash -> ");
  Serial.print(fillPercent, 0);
  Serial.print("% full (empty = ");
  Serial.print(emptyDistanceCm, 1);
  Serial.print(" cm)");
  if (fillPercent >= FULL_LEVEL_PERCENT) {
    Serial.print("  *** BIN FULL ***");
  }
  Serial.println();
}

void checkSerialCommand() {
  if (!Serial.available()) return;
  char command = Serial.read();
  if (command != 'c' && command != 'C') return;

  if (!lidIsClosed) {
    Serial.println("Close the lid first, then type c again.");
    return;
  }
  float distanceCm = readFillDistanceCm();
  if (distanceCm < FULL_DISTANCE_CM + 2) {
    Serial.println("Calibration failed: no good reading. Is the bin empty?");
    return;
  }
  emptyDistanceCm = distanceCm;
  EEPROM.put(SAVED_EMPTY_DISTANCE_ADDRESS, emptyDistanceCm);
  Serial.print("Calibrated and saved: empty bin = ");
  Serial.print(emptyDistanceCm, 1);
  Serial.println(" cm");
}

void loadSavedCalibration() {
  float savedDistanceCm = 0;
  EEPROM.get(SAVED_EMPTY_DISTANCE_ADDRESS, savedDistanceCm);

  if (savedDistanceCm >= FULL_DISTANCE_CM + 2 && savedDistanceCm <= LONGEST_VALID_DISTANCE_CM) {
    emptyDistanceCm = savedDistanceCm;
    Serial.print("Loaded saved calibration: empty bin = ");
    Serial.print(emptyDistanceCm, 1);
    Serial.println(" cm");
  } else {
    Serial.println("No calibration saved yet. With the bin EMPTY and lid CLOSED, type c and press Enter.");
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(HAND_SENSOR_TRIG_PIN, OUTPUT);
  pinMode(HAND_SENSOR_ECHO_PIN, INPUT);
  pinMode(FILL_SENSOR_TRIG_PIN, OUTPUT);
  pinMode(FILL_SENSOR_ECHO_PIN, INPUT);
  digitalWrite(HAND_SENSOR_TRIG_PIN, LOW);
  digitalWrite(FILL_SENSOR_TRIG_PIN, LOW);

  lidServo.attach(SERVO_PIN, 500, 2400);
  lidServo.write(LID_CLOSED_ANGLE);
  lidClosedTime = millis();

  Serial.println("Fill + hand + servo test started (UNO R4 WiFi).");
  loadSavedCalibration();
}

void loop() {
  unsigned long now = millis();
  checkSerialCommand();
  checkHand(now);
  moveServo(now);
  checkFill(now);
}
