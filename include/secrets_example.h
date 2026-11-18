#pragma once

const char WIFI_NAME[] = "your-2.4GHz-hotspot-name";
const char WIFI_PASSWORD[] = "your-hotspot-password";

// Cloud server: domain name and port 443 (HTTPS).
// Laptop server instead: the laptop's IP (e.g. "192.168.1.206") and port 5001 (plain HTTP).
const char SERVER_ADDRESS[] = "smartbin-t27.duckdns.org";
const int SERVER_PORT = 443;

// Signs every report so the server knows it really came from this bin.
// Must match this bin's entry in DEVICE_KEYS in server/.env (bin-1=hmac:<this value>).
// Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
const char DEVICE_HMAC_KEY[] = "replace-with-a-long-random-secret";
