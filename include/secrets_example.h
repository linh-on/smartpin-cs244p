#pragma once

const char WIFI_NAME[] = "your-2.4GHz-hotspot-name";
const char WIFI_PASSWORD[] = "your-hotspot-password";

const char SERVER_ADDRESS[] = "172.20.10.2";
const int SERVER_PORT = 5001;

// Signs every report so the server knows it really came from this bin.
// Must match this bin's entry in DEVICE_KEYS in server/.env (bin-1=hmac:<this value>).
// Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
const char DEVICE_HMAC_KEY[] = "replace-with-a-long-random-secret";
