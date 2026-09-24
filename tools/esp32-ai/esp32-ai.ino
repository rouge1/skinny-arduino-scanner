#include <WiFi.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <vector>
#include <algorithm>
#include <stdarg.h>
#include <Arduino.h>

// Serial runs at 460800 baud (the BLE payloads are sent as hex).
// Serial commands (single characters, case-insensitive):
//   b = Bluetooth only   w = WiFi only   x = both   s = stop scanning
//   j = JSON-lines output (the desktop app sends this; see "Standalone")
//   t = text tables (default)   ? = print a status/hello line
//   m = same as a short BOOT press   k = same as a long BOOT press
//   d = dump the survey log   c = clear the survey log
//
// Survey marks: a short press of the BOOT button starts a capture at the
// current spot, the next short press stops it; a long press (>= 1 s) skips
// the spot (and cancels a capture in progress). A capture is rejected when
// no complete scan of each scanned kind fitted inside it. Events carry the
// board's millis() ("t", scan "t0"/"t1") so captures and scans are matched
// in board time, whatever the serial latency.
//
// Standalone (e.g. on a power bank): until an app sends 'j', captures are
// also appended to /survey.jsonl in flash as the same JSON lines the app
// would get, with a {"ev":"boot"} line after each power-up. The app imports
// them later with 'd' (each log line is sent prefixed with "L", between
// log_begin/log_end events) and 'c' clears the log.
//
// LED: solid while capturing; after a capture or skip, N slow blinks = the
// stop number just finished (also shown at power-up: stops already logged);
// fast flicker = capture rejected (too short) or log full; a skip starts
// with three quick flashes; otherwise a short blip every 2 s (alive).

static const uint32_t BLE_SCAN_SECONDS = 5;
static const int LED_PIN = 2;
static const int BUTTON_PIN = 0;  // BOOT; only a strapping pin during reset
static const uint32_t LONG_PRESS_MS = 1000;
static const int FW_VERSION = 5;
static const size_t MAX_BLE_DEVICES = 256;  // cap per scan so a crowded area can't exhaust the heap
static const char *LOG_PATH = "/survey.jsonl";

enum Mode { MODE_BT, MODE_WIFI, MODE_BOTH, MODE_IDLE };
static Mode g_mode = MODE_BOTH;
static bool g_json = false;
static bool g_host = false;  // an app sent 'j' since boot: captures aren't logged to flash
static volatile bool g_scanning = false;
static volatile bool g_marking = false;
static uint32_t g_markCount = 0;
static uint32_t g_markT0 = 0;
static int g_markWifi = 0, g_markBle = 0;  // complete scans inside the current capture
static bool g_scanLogged = false;          // the scan in progress started during a capture

// LED patterns, consumed by ledTask.
static volatile int g_flash = 0;           // quick toggles left
static volatile int g_count = 0;           // slow blinks left
static volatile uint32_t g_errUntil = 0;   // flicker until this millis()

// Flash log.
static bool g_fsOk = false;
static File g_log;
static bool g_bootLogged = false;
static uint32_t g_stops = 0;  // captures + skips in the log (persisted, for the LED)
static Preferences g_prefs;

// Output comes from the main loop and the button task. Every line is built
// in g_line and emitted while holding this recursive lock, so lines never
// interleave and the shared buffer is safe.
static SemaphoreHandle_t g_out;
struct OutLock {
  OutLock() { xSemaphoreTakeRecursive(g_out, portMAX_DELAY); }
  ~OutLock() { xSemaphoreGiveRecursive(g_out); }
};

static void ledTask(void *) {
  pinMode(LED_PIN, OUTPUT);
  uint32_t tick = 0;
  int phase = 0;
  for (;;) {  // 50 ms per tick
    tick++;
    bool on;
    if ((int32_t)(g_errUntil - millis()) > 0) {
      on = tick % 2;
      phase = 0;
    } else if (g_flash > 0) {
      on = g_flash % 2;
      if (tick % 2 == 0) g_flash--;
      phase = 0;
    } else if (g_count > 0) {
      on = phase < 6;  // 300 ms on, 300 ms off
      if (++phase == 12) {
        phase = 0;
        g_count--;
      }
    } else if (g_marking) {
      on = true;
    } else {
      on = tick % 40 == 0;
    }
    digitalWrite(LED_PIN, on);
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

static void ledError() { g_errUntil = millis() + 2000; }

// One BLE device per scan. Fixed size (no heap Strings) so the whole list is
// allocated once in setup(). The host decodes the raw advertising payload
// (AD structures: name, company, services, beacons...); the firmware only
// looks inside it for the name in text mode.
static const size_t MAX_PAYLOAD = 62;  // legacy advertising data + scan response
struct Found {
  uint8_t addr[6];  // most significant octet first, as displayed
  uint8_t atype;    // esp_ble_addr_type_t: 0 public, 1 random, 2/3 RPA
  int8_t rssi;
  uint8_t len;
  uint8_t payload[MAX_PAYLOAD];
};
static std::vector<Found> g_bt;

struct Net {
  String ssid;
  String bssid;
  int rssi;
  int ch;
  String sec;
};
static std::vector<Net> g_wifi;

class ScanCB : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice dev) override {
    BLEAddress a = dev.getAddress();
    const uint8_t *addr = a.getNative();
    Found *f = nullptr;
    for (auto &e : g_bt) {
      if (memcmp(e.addr, addr, 6) == 0) {
        f = &e;
        break;
      }
    }
    if (!f) {
      if (g_bt.size() >= MAX_BLE_DEVICES) return;
      g_bt.emplace_back();
      f = &g_bt.back();
      memcpy(f->addr, addr, 6);
      f->len = 0;
    }
    f->atype = dev.getAddressType();
    f->rssi = dev.getRSSI();
    // Keep the longest payload seen this scan: advertisements that came with
    // a scan response carry both, and so usually the name.
    size_t len = min(dev.getPayloadLength(), MAX_PAYLOAD);
    if (len >= f->len) {
      memcpy(f->payload, dev.getPayload(), len);
      f->len = len;
    }
  }
};

static const char *authStr(wifi_auth_mode_t t) {
  switch (t) {
    case WIFI_AUTH_OPEN: return "OPEN";
    case WIFI_AUTH_WEP: return "WEP";
    case WIFI_AUTH_WPA_PSK: return "WPA";
    case WIFI_AUTH_WPA2_PSK: return "WPA2";
    case WIFI_AUTH_WPA_WPA2_PSK: return "WPA/2";
    case WIFI_AUTH_WPA2_ENTERPRISE: return "WPA2-EAP";
    case WIFI_AUTH_WPA3_PSK: return "WPA3";
    case WIFI_AUTH_WPA2_WPA3_PSK: return "WPA2/3";
    default: return "?";
  }
}

static const char *modeKey(Mode m) {
  switch (m) {
    case MODE_BT: return "bt";
    case MODE_WIFI: return "wifi";
    case MODE_BOTH: return "both";
    default: return "idle";
  }
}

static const char *modeLabel(Mode m) {
  switch (m) {
    case MODE_BT: return "Bluetooth";
    case MODE_WIFI: return "WiFi";
    case MODE_BOTH: return "WiFi + Bluetooth";
    default: return "Stopped";
  }
}

// ---- line output -----------------------------------------------------------

static char g_line[768];
static size_t g_len = 0;

static void lineStart() { g_len = 0; }

static void lineAdd(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(g_line + g_len, sizeof(g_line) - g_len, fmt, ap);
  va_end(ap);
  if (n > 0) g_len = min(g_len + (size_t)n, sizeof(g_line) - 1);
}

// Appends s as a quoted JSON string.
static void lineStr(const char *s) {
  lineAdd("\"");
  for (; *s; s++) {
    uint8_t c = *s;
    if (c == '"' || c == '\\') lineAdd("\\%c", c);
    else if (c < 0x20) lineAdd("\\u%04x", c);
    else lineAdd("%c", c);
  }
  lineAdd("\"");
}

static bool logEnabled() { return g_fsOk && !g_host; }

static bool logFull() {
  return g_fsOk && LittleFS.usedBytes() > LittleFS.totalBytes() * 95 / 100;
}

static void logWrite(const char *s, size_t n) {
  if (!logEnabled()) return;
  if (!g_log) g_log = LittleFS.open(LOG_PATH, FILE_APPEND);
  if (!g_log) return;
  if (!g_bootLogged) {
    g_bootLogged = true;
    g_log.printf("{\"ev\":\"boot\",\"ver\":%d,\"mode\":\"%s\"}\n", FW_VERSION, modeKey(g_mode));
  }
  g_log.write((const uint8_t *)s, n);
  g_log.write('\n');
}

static void logFlush() {
  if (g_log) g_log.flush();
}

// Sends the built line to serial (JSON mode only) and/or the flash log.
static void lineEnd(bool toLog) {
  if (g_json) {
    Serial.write((const uint8_t *)g_line, g_len);
    Serial.write('\n');
  }
  if (toLog) logWrite(g_line, g_len);
}

static uint32_t logBytes() {
  if (!g_fsOk) return 0;
  File f = LittleFS.open(LOG_PATH, FILE_READ);
  uint32_t n = f ? f.size() : 0;
  if (f) f.close();
  return n;
}

// ---- events ----------------------------------------------------------------

static void printHello() {
  OutLock lock;
  if (g_json) {
    lineStart();
    lineAdd("{\"ev\":\"hello\",\"fw\":\"esp32-ai\",\"ver\":%d,\"mode\":\"%s\",\"marking\":%s,"
            "\"t\":%lu,\"stops\":%lu,\"log_bytes\":%lu,\"fs\":%s,\"mac\":",
            FW_VERSION, modeKey(g_mode), g_marking ? "true" : "false", (unsigned long)millis(),
            (unsigned long)g_stops, (unsigned long)logBytes(), g_fsOk ? "true" : "false");
    lineStr(WiFi.macAddress().c_str());
    lineAdd("}");
    lineEnd(false);
  } else {
    Serial.printf("\n[status] esp32-ai v%d  mode=%s%s  survey log: %lu stops, %lu bytes\n",
                  FW_VERSION, modeLabel(g_mode), g_marking ? "  (capturing)" : "",
                  (unsigned long)g_stops, (unsigned long)logBytes());
  }
}

// action: start / stop / reject / skip / full; t: millis() of the press.
static void printButton(const char *action, uint32_t t) {
  OutLock lock;
  lineStart();
  lineAdd("{\"ev\":\"button\",\"action\":\"%s\",\"mark\":%lu,\"t\":%lu,\"wifi\":%d,\"ble\":%d}",
          action, (unsigned long)g_markCount, (unsigned long)t, g_markWifi, g_markBle);
  lineEnd(true);
  logFlush();
  if (!g_json) {
    Serial.printf("\n[button] %s (mark %lu, %d WiFi + %d BLE scans)\n", action,
                  (unsigned long)g_markCount, g_markWifi, g_markBle);
  }
}

static void saveStops() { g_prefs.putUInt("stops", g_stops); }

static void shortPress(uint32_t t) {
  if (!g_marking) {
    if (logEnabled() && logFull()) {
      ledError();
      printButton("full", t);
      return;
    }
    g_markCount++;
    g_markT0 = t;
    g_markWifi = g_markBle = 0;
    g_marking = true;
    printButton("start", t);
    return;
  }
  g_marking = false;
  bool needW = g_mode == MODE_WIFI || g_mode == MODE_BOTH;
  bool needB = g_mode == MODE_BT || g_mode == MODE_BOTH;
  if ((needW && !g_markWifi) || (needB && !g_markBle) || g_mode == MODE_IDLE) {
    ledError();
    printButton("reject", t);
    return;
  }
  g_stops++;
  saveStops();
  g_count = g_stops;
  printButton("stop", t);
}

static void longPress(uint32_t t) {
  g_marking = false;
  g_stops++;
  saveStops();
  g_flash = 6;
  g_count = g_stops;
  printButton("skip", t);
}

// Polls BOOT (active low). A press counts on release (so a long press never
// toggles first); a hold of LONG_PRESS_MS fires the skip while still held.
static void buttonTask(void *) {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  bool down = false, longFired = false;
  uint32_t tDown = 0, tUp = 0;
  for (;;) {
    bool pressed = digitalRead(BUTTON_PIN) == LOW;
    uint32_t now = millis();
    if (pressed && !down && now - tUp > 80) {  // ignore release bounce
      down = true;
      longFired = false;
      tDown = now;
    } else if (pressed && down && !longFired && now - tDown >= LONG_PRESS_MS) {
      longFired = true;
      longPress(tDown);
    } else if (!pressed && down) {
      down = false;
      tUp = now;
      if (!longFired && now - tDown >= 30) shortPress(tDown);
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

static void dumpLog() {
  OutLock lock;
  if (g_log) g_log.close();
  File f = g_fsOk ? LittleFS.open(LOG_PATH, FILE_READ) : File();
  Serial.printf("{\"ev\":\"log_begin\",\"bytes\":%lu,\"stops\":%lu}\n",
                (unsigned long)(f ? f.size() : 0), (unsigned long)g_stops);
  static char buf[800];
  while (f && f.available()) {
    size_t n = f.readBytesUntil('\n', buf, sizeof(buf));
    Serial.write('L');
    Serial.write((const uint8_t *)buf, n);
    Serial.write('\n');
  }
  if (f) f.close();
  Serial.println("{\"ev\":\"log_end\"}");
}

static void clearLog() {
  OutLock lock;
  if (g_log) g_log.close();
  if (g_fsOk) LittleFS.remove(LOG_PATH);
  g_bootLogged = false;
  g_stops = 0;
  saveStops();
  Serial.println("{\"ev\":\"log_cleared\"}");
}

static void printMode() {
  OutLock lock;
  if (g_json) {
    Serial.printf("{\"ev\":\"mode\",\"mode\":\"%s\"}\n", modeKey(g_mode));
  } else {
    Serial.printf("\n[mode] %s\n", modeLabel(g_mode));
  }
}

void handleSerial() {
  while (Serial.available()) {
    int c = tolower(Serial.read());
    Mode m = g_mode;
    switch (c) {
      case 'b': m = MODE_BT; break;
      case 'w': m = MODE_WIFI; break;
      case 'x': m = MODE_BOTH; break;
      case 's': m = MODE_IDLE; break;
      case 'j':
        g_json = true;
        g_host = true;
        if (g_log) g_log.close();
        printHello();
        continue;
      case 't': g_json = false; printHello(); continue;
      case '?': printHello(); continue;
      case 'm': shortPress(millis()); continue;
      case 'k': longPress(millis()); continue;
      case 'd': dumpLog(); continue;
      case 'c': clearLog(); continue;
      default: continue;
    }
    if (m != g_mode) {
      g_mode = m;
      printMode();
    }
  }
}

// ---- scans -----------------------------------------------------------------

static void scanStarted(const char *kind, uint32_t n, uint32_t t0) {
  g_scanLogged = g_marking;
  OutLock lock;
  lineStart();
  lineAdd("{\"ev\":\"scan_start\",\"kind\":\"%s\",\"scan\":%lu,\"t\":%lu}", kind,
          (unsigned long)n, (unsigned long)t0);
  lineEnd(g_scanLogged);
}

// Counts a finished scan toward the capture it ran inside of.
static void scanFinished(bool wifi, uint32_t t0) {
  if (g_marking && t0 >= g_markT0) {
    if (wifi) g_markWifi++;
    else g_markBle++;
  }
}

// t0/t1: millis() when the scan started and finished (before printing).
static void printScanDone(const char *kind, uint32_t n, size_t count, uint32_t t0, uint32_t t1) {
  lineStart();
  lineAdd("{\"ev\":\"scan_done\",\"kind\":\"%s\",\"scan\":%lu,\"count\":%u,\"ms\":%lu,"
          "\"t0\":%lu,\"t1\":%lu,\"heap\":%lu}",
          kind, (unsigned long)n, (unsigned)count, (unsigned long)(t1 - t0),
          (unsigned long)t0, (unsigned long)t1, (unsigned long)ESP.getFreeHeap());
  lineEnd(g_scanLogged);
  if (g_scanLogged) logFlush();
}

static void fmtAddr(const uint8_t *a, char *out) {
  snprintf(out, 18, "%02x:%02x:%02x:%02x:%02x:%02x", a[0], a[1], a[2], a[3], a[4], a[5]);
}

// The Complete (0x09) or else Shortened (0x08) Local Name AD structure, for
// the text tables. Returns the name's length and points *name at it.
static size_t findName(const uint8_t *p, size_t len, const char **name) {
  size_t best = 0;
  for (size_t i = 0; i + 1 < len && p[i];) {
    size_t n = p[i];
    uint8_t t = p[i + 1];
    if (i + 1 + n > len) break;
    if (t == 0x09 || (t == 0x08 && !best)) {
      *name = (const char *)&p[i + 2];
      best = n - 1;
      if (t == 0x09) break;
    }
    i += 1 + n;
  }
  return best;
}

void printBt(uint32_t n, uint32_t t0, uint32_t t1) {
  std::sort(g_bt.begin(), g_bt.end(),
            [](const Found &a, const Found &b) { return a.rssi > b.rssi; });
  char addr[18];
  OutLock lock;

  if (g_json || g_scanLogged) {
    for (auto &f : g_bt) {
      fmtAddr(f.addr, addr);
      lineStart();
      lineAdd("{\"ev\":\"ble\",\"scan\":%lu,\"addr\":\"%s\",\"at\":%u,\"rssi\":%d,\"adv\":\"",
              (unsigned long)n, addr, f.atype, f.rssi);
      for (size_t i = 0; i < f.len; i++) lineAdd("%02x", f.payload[i]);
      lineAdd("\"}");
      lineEnd(g_scanLogged);
    }
    printScanDone("ble", n, g_bt.size(), t0, t1);
  }
  if (g_json) return;

  Serial.printf("\n=== BLE scan #%lu  (%lu devices) ===\n",
                (unsigned long)n, (unsigned long)g_bt.size());
  Serial.println("  #   RSSI  ADDRESS              NAME");
  Serial.println("----  ----  -------------------  --------------------------------");
  int i = 1;
  for (auto &f : g_bt) {
    const char *name = nullptr;
    size_t len = findName(f.payload, f.len, &name);
    fmtAddr(f.addr, addr);
    Serial.printf("%4d  %4d  %-19s  %.*s\n", i++, f.rssi, addr, (int)(len ? len : 9),
                  len ? name : "(unnamed)");
  }
  if (g_bt.empty()) Serial.println("  (no devices found)");
  Serial.println();
}

void printWifi(uint32_t n, uint32_t t0, uint32_t t1) {
  std::sort(g_wifi.begin(), g_wifi.end(),
            [](const Net &a, const Net &b) { return a.rssi > b.rssi; });
  OutLock lock;

  if (g_json || g_scanLogged) {
    for (auto &w : g_wifi) {
      lineStart();
      lineAdd("{\"ev\":\"wifi\",\"scan\":%lu,\"bssid\":\"%s\",\"rssi\":%d,\"ch\":%d,"
              "\"sec\":\"%s\",\"ssid\":",
              (unsigned long)n, w.bssid.c_str(), w.rssi, w.ch, w.sec.c_str());
      lineStr(w.ssid.c_str());
      lineAdd("}");
      lineEnd(g_scanLogged);
    }
    printScanDone("wifi", n, g_wifi.size(), t0, t1);
  }
  if (g_json) return;

  Serial.printf("\n=== WiFi scan #%lu  (%lu networks) ===\n",
                (unsigned long)n, (unsigned long)g_wifi.size());
  Serial.println("  #   RSSI  CH  SEC      SSID");
  Serial.println("----  ----  --  -------  --------------------------------");
  int i = 1;
  for (auto &w : g_wifi) {
    Serial.printf("%4d  %4d  %2d  %-7s  %s\n", i++, w.rssi, w.ch, w.sec.c_str(),
                  w.ssid.length() ? w.ssid.c_str() : "(hidden)");
  }
  if (g_wifi.empty()) Serial.println("  (no networks found)");
  Serial.println();
}

void doWifiScan(uint32_t n) {
  g_wifi.clear();
  uint32_t t = millis();
  scanStarted("wifi", n, t);
  g_scanning = true;
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  int found = WiFi.scanNetworks(false, true);  // blocking, include hidden networks
  for (int i = 0; i < found; i++) {
    Net w;
    w.ssid = WiFi.SSID(i);
    w.bssid = WiFi.BSSIDstr(i);
    w.rssi = WiFi.RSSI(i);
    w.ch = WiFi.channel(i);
    w.sec = authStr(WiFi.encryptionType(i));
    g_wifi.push_back(w);
  }
  WiFi.scanDelete();
  g_scanning = false;
  uint32_t t1 = millis();
  scanFinished(true, t);
  printWifi(n, t, t1);
}

void doBleScan(uint32_t n) {
  g_bt.clear();
  uint32_t t = millis();
  scanStarted("ble", n, t);
  g_scanning = true;
  BLEScan *scan = BLEDevice::getScan();
  scan->start(BLE_SCAN_SECONDS, false);
  scan->clearResults();
  g_scanning = false;
  uint32_t t1 = millis();
  scanFinished(false, t);
  printBt(n, t, t1);
}

void setup() {
  g_out = xSemaphoreCreateRecursiveMutex();
  Serial.begin(460800);
  delay(500);
  Serial.println();
  Serial.println("ESP32 Collector - WiFi + Bluetooth scanner");
  Serial.println("Send: b=bluetooth  w=wifi  x=both (default)  s=stop  j=json  t=text");
  Serial.println("BOOT button: press = start/stop capture, hold 1 s = skip spot");

  g_fsOk = LittleFS.begin(true);  // formats the partition the first time
  g_prefs.begin("survey", false);
  g_stops = g_prefs.getUInt("stops", 0);
  if (!g_fsOk) Serial.println("[survey] flash filesystem unavailable: captures won't be logged");
  Serial.printf("[survey] log: %lu stops, %lu bytes\n", (unsigned long)g_stops,
                (unsigned long)logBytes());

  g_bt.reserve(MAX_BLE_DEVICES);

  BLEDevice::init("");
  BLEScan *scan = BLEDevice::getScan();
  // wantDuplicates=true stops the library from keeping its own copy of every
  // device it sees (ScanCB already de-duplicates into g_bt).
  scan->setAdvertisedDeviceCallbacks(new ScanCB(), true);
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(99);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);

  // Power-up LED: the stop count already logged, or two quick flashes if none.
  if (g_stops) g_count = g_stops;
  else g_flash = 4;
  xTaskCreatePinnedToCore(ledTask, "led", 2048, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(buttonTask, "button", 8192, NULL, 2, NULL, 1);
}

void loop() {
  static uint32_t btCount = 0;
  static uint32_t wifiCount = 0;

  handleSerial();

  // Scans run back to back (no idle padding) so a short survey capture
  // still gets several of each.
  if (g_mode == MODE_IDLE) {
    delay(20);
  } else if (g_mode == MODE_WIFI) {
    doWifiScan(++wifiCount);
  } else if (g_mode == MODE_BT) {
    doBleScan(++btCount);
  } else {
    doWifiScan(++wifiCount);
    handleSerial();
    if (g_mode == MODE_BOTH) doBleScan(++btCount);
  }
}
