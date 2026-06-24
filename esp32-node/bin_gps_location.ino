#include <TinyGPS++.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>

// ===== >>> ISI BAGIAN INI <<< =====
const char* WIFI_SSID   = "Kosan Pa Nendi lt2";
const char* WIFI_PASS   = "GBABlok05";

const char* URL_BASE    = "https://GANTI.ngrok-free.dev"; 
const char* ADMIN_EMAIL = "admin@smartbin.local";              
const char* ADMIN_PASS  = "admin123";
const char* BIN_ID      = "GANTI_CUID_BIN";        

// ================= GPS (UART2) =================
TinyGPSPlus gps;
HardwareSerial gpsSerial(2);
static const int GPS_RX_PIN = 16;
static const int GPS_TX_PIN = 17;
static const int GPS_BAUD   = 38400;   // ganti 9600 kalau modul Cyclone

bool   uartOk = false;
long   milisTerakhirNmea = 0;
const long waktuHabisUart = 3000;

bool   lokasiTerakhirValid = false;
double lintangTerakhir = 0, bujurTerakhir = 0;
int    satelitTerakhir = 0;

// ================= KIRIM BERKALA =================
long waktuKirimTerakhir = 0;
const long intervalKirim = 300000;   // 5 menit — tong statis, tak perlu sering

WiFiClientSecure klienAman;
String authToken = "";

// ================= WIFI =================
void konekWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting WiFi");
  int coba = 0;
  while (WiFi.status() != WL_CONNECTED && coba < 40) {
    delay(500); Serial.print("."); coba++;
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi OK, IP="); Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi GAGAL (lanjut tanpa kirim).");
  }
}

// ================= GPS =================
void bacaGps() {
  while (gpsSerial.available()) {
    gps.encode(gpsSerial.read());
    milisTerakhirNmea = millis();
    uartOk = true;
  }
  if (millis() - milisTerakhirNmea > waktuHabisUart) uartOk = false;

  if (gps.location.isValid()) {
    lokasiTerakhirValid = true;
    lintangTerakhir     = gps.location.lat();
    bujurTerakhir       = gps.location.lng();
    satelitTerakhir     = gps.satellites.value();
  }
}

// ================= LOGIN ADMIN =================
bool loginAdmin() {
  if (WiFi.status() != WL_CONNECTED) return false;

  String body = String("{\"email\":\"") + ADMIN_EMAIL +
                "\",\"password\":\"" + ADMIN_PASS + "\"}";

  HTTPClient http;
  klienAman.setInsecure();   // demo cepat — skip verifikasi sertifikat
  http.begin(klienAman, String(URL_BASE) + "/auth/login");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("ngrok-skip-browser-warning", "true");

  int kode = http.POST(body);
  String resp = http.getString();
  http.end();

  Serial.print("[LOGIN "); Serial.print(kode); Serial.println("]");
  if (kode != 200) { Serial.println(resp); return false; }

  // Ambil token dari JSON: ..."token":"XXXX"...
  int i = resp.indexOf("\"token\":\"");
  if (i < 0) { Serial.println("[LOGIN] token tidak ditemukan"); return false; }
  i += 9;
  int j = resp.indexOf("\"", i);
  if (j < 0) return false;
  authToken = resp.substring(i, j);
  Serial.println("[LOGIN] token didapat");
  return true;
}

// ================= KIRIM LOKASI =================
void kirimLokasi() {
  if (WiFi.status() != WL_CONNECTED) { Serial.println("[LOC] WiFi off, batal."); return; }
  if (!lokasiTerakhirValid)          { Serial.println("[LOC] GPS belum fix, batal."); return; }
  if (authToken == "" && !loginAdmin()) { Serial.println("[LOC] login gagal, batal."); return; }

  String payload = String("{\"lat\":") + String(lintangTerakhir, 6) +
                   ",\"lng\":" + String(bujurTerakhir, 6) + "}";

  HTTPClient http;
  klienAman.setInsecure();
  http.begin(klienAman, String(URL_BASE) + "/bins/" + BIN_ID);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("ngrok-skip-browser-warning", "true");
  http.addHeader("Authorization", "Bearer " + authToken);

  int kode = http.sendRequest("PUT", payload);
  Serial.print("[LOC PUT "); Serial.print(kode); Serial.print("] "); Serial.println(payload);
  Serial.println(http.getString());
  http.end();

  // token kadaluarsa / invalid -> login ulang sekali lalu coba lagi
  if (kode == 401) {
    Serial.println("[LOC] token invalid, login ulang...");
    authToken = "";
    if (loginAdmin()) kirimLokasi();
  }
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);

  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  milisTerakhirNmea = millis();

  konekWifi();

  Serial.println("SmartBin GPS Location Reporter aktif!");
  Serial.println("Ketik: lokasi  -> lihat koordinat | kirim -> POST lokasi sekarang");
}

// ================= LOOP =================
void loop() {
  bacaGps();

  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd == "kirim") {
      kirimLokasi();
    } else if (cmd == "lokasi") {
      Serial.print("[GPS] ");
      if (lokasiTerakhirValid) {
        Serial.print("Lat: "); Serial.print(lintangTerakhir, 6);
        Serial.print(" Lng: "); Serial.print(bujurTerakhir, 6);
        Serial.print(" Sat: "); Serial.println(satelitTerakhir);
      } else {
        Serial.println("(belum fix)");
      }
    } else if (cmd.length() > 0) {
      Serial.println("Input tidak dikenal! (lokasi / kirim)");
    }
  }

  // kirim lokasi berkala
  if (millis() - waktuKirimTerakhir >= intervalKirim) {
    waktuKirimTerakhir = millis();
    kirimLokasi();
  }
}
