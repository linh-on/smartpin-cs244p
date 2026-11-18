# Smart Bin

Arduino UNO R4 WiFi trash can: opens the lid when a hand is near, measures how full the bin is, and reports to a small web dashboard.

```
src/main.cpp     firmware (PlatformIO)
server/          FastAPI + SQLite dashboard
tools/           standalone hardware test sketch (Arduino IDE)
```

## Wiring

| Part | Pin | Arduino |
|------|-----|---------|
| Hand sensor (HC-SR04) | VCC / GND | 5V / GND |
| | TRIG / ECHO | D2 / D3 |
| Fill sensor (HC-SR04, inside the bin, facing down) | VCC / GND | 5V / GND |
| | TRIG / ECHO | D4 / D5 |
| Servo | Red / Brown | 5V / GND |
| | Signal (orange) | D9 |


## Firmware

1. Copy `include/secrets_example.h` to `include/secrets.h` and fill in the Wi-Fi (2.4 GHz), the server address and port, this kit's `DEVICE_ID` (`bin-1`, `bin-2`, ...: one per kit) and `DEVICE_HMAC_KEY`.
2. Build and upload with PlatformIO, then open the serial monitor (115200 baud):
   ```bash
   pio run -t upload
   pio device monitor -b 115200
   ```
3. Calibrate: with the bin empty and the lid closed, note the `Fill: xx cm` reading and set `EMPTY_BIN_DISTANCE_CM` in `src/main.cpp` to it.

## Server

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then set SESSION_SECRET and DEVICE_KEYS
python manage_users.py add <your name> bin-1    # asks for a password
python app.py             # dashboard at http://<this computer's IP>:5001, API docs at /docs
```

The board and the computer must be on the same network, and `SERVER_PORT` in `secrets.h` must match the server port (default 5001; 5000 is taken by AirPlay on macOS).

Every report from the bin is signed. The bin computes HMAC-SHA256 over the JSON body with `DEVICE_HMAC_KEY` and sends it in an `X-Signature` header; the server checks it against the same secret in `DEVICE_KEYS` and rejects anything unsigned, changed, or from an unknown device. `DEVICE_KEYS` also accepts an ATECC608A public key (`bin-1=ecdsa:<128 hex chars>`) for when the bin signs with the secure element instead. Accepted reports and their signatures are kept in the `messages` table.

### Accounts

Each person logs in to the dashboard with their own username and password, and only sees the bins linked to their account. Bins never use usernames or passwords: a bin proves who it is by signing its reports.

```bash
python manage_users.py add linh bin-2               # new account (asks for the password)
python manage_users.py devices sam bin-1 bin-2      # change which bins an account sees
python manage_users.py passwd linh                  # change a password
python manage_users.py remove sam                   # delete an account (its bins' data stays)
python manage_users.py list
```

Passwords are stored only as salted scrypt hashes. Each bin has its own full-level setting, so changing yours never changes a teammate's bin. A bin can be linked to several accounts if teammates want to share one.

On the cloud server, run the same commands inside the app container, from `deploy/`:

```bash
git pull
sudo docker compose up -d --build
sudo docker compose exec app python manage_users.py add linh bin-2
```

To add a new kit: give it a new `DEVICE_ID` in its `secrets.h`, add its key to `DEVICE_KEYS` in `server/.env`, restart the server, and link it to an account.

Tests: `pip install -r requirements-dev.txt && pytest`

## Hardware test sketch

`tools/servo-and-sensors-only/` tests the sensors and servo without Wi-Fi. Open it in Arduino IDE, select **Arduino UNO R4 WiFi**, and upload. With the bin empty and the lid closed, type `c` in the Serial Monitor to calibrate.
