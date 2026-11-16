# Smart Bin

Arduino UNO R4 WiFi: opens the lid when a hand is near, and reports how full the bin is.

## Wiring

| Part | Pin | Arduino |
|------|-----|---------|
| Hand sensor (HC-SR04) | VCC / GND | 5V / GND |
| | TRIG | D2 |
| | ECHO | D3 |
| Fill sensor (HC-SR04, inside lid facing down) | VCC / GND | 5V / GND |
| | TRIG | D4 |
| | ECHO | D5 |
| Servo | Red / Brown | 5V / GND |
| | Signal (orange) | D9 |

If the servo jitters or the board resets, power the servo from an external 5V supply and connect its GND to the Arduino GND.

## Run

Open `servo-and-sensors-only/servo-and-sensors-only.ino` in Arduino IDE, select **Arduino UNO R4 WiFi**, and upload. Serial Monitor: 115200 baud.

First time: with the bin empty and lid closed, type `c` in Serial Monitor to calibrate.
