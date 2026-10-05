// ========================================================
// STM32_SENSOR.ino — WeAct STM32F405RGT6 (64-pin CoreBoard)
// Pin mapping sesuai schematic V1.1
// + INA219 Battery Monitor (Li-Ion 3S 18650, 6800mAh, 12V)
// + HX711 Load Cell (PB8=DT, PB9=SCK)
//

#include <Wire.h>
#include "Adafruit_VL53L0X.h"
#include <TinyGPS++.h>
#include <ArduinoJson.h>
#include <Servo.h>
#include <Adafruit_INA219.h>
#include <HX711_ADC.h>  // by Olav Kallhovd — install via Library Manager

// ===== IDENTITAS =====
const char* NODE_ID = "bin-003";
const char* LOCATION = "Gasibu";
const char* AREA_ID  = "cmmm4blt30007x9qth890ceux";

// ========================================================
// PIN MAP — WeAct F405 64-pin
// ========================================================

// --- I2C1: VL53L0X Organik + B3 ---
#define VL53_SCL PB6
#define VL53_SDA PB7

// --- I2C3: VL53L0X Anorganik + INA219 ---
TwoWire Wire3(PC9, PA8);

// --- XSHUT VL53L0X ---
#define XSHUT_ORGANIK   PA0
#define XSHUT_ANORGANIK PA2
#define XSHUT_B3        PA4

// --- GPS + Bridge UART ---
HardwareSerial SERIAL_GPS(PA10, PA9);
HardwareSerial SERIAL_ESP(PC11, PC10);
#define SERIAL_DEBUG Serial

// --- Stepper ---
#define DIR_PIN   PA6
#define STEP_PIN  PA7
#define ENA_PIN   PC4
#define STPR      3200

// --- Servo ---
#define SERVO_PIN    PC6
#define SERVO_DATAR  0
#define SERVO_MIRING 90
#define TILT_DELAY   1500

// --- HX711 Load Cell ---
// PB8, PB9 — right header, bebas conflict
#define HX711_DT  PB8
#define HX711_SCK PB9

// Set 1 untuk mencetak lama eksekusi tiap fungsi di loop(). Berguna bila
// telemetri datang jauh lebih lambat dari SENSOR_INTERVAL.
#define DEBUG_TIMING 0

// ========================================================
// VL53L0X
// ========================================================
#define ADDR_ORGANIK    0x30
#define ADDR_ANORGANIK  0x31
#define ADDR_B3         0x32

Adafruit_VL53L0X loxOrganik;
Adafruit_VL53L0X loxAnorganik;
Adafruit_VL53L0X loxB3;

bool  sensorOkOrganik   = false;
bool  sensorOkAnorganik = false;
bool  sensorOkB3        = false;

float jarakOrganik    = -1, jarakAnorganik = -1, jarakB3 = -1;
float jarakKosongOrganik = 0, jarakKosongAnorganik = 0, jarakKosongB3 = 0;
bool  kalibrasiOkOrganik = false, kalibrasiOkAnorganik = false, kalibrasiOkB3 = false;

// ========================================================
// GPS
// ========================================================
TinyGPSPlus gps;
bool   lokasiValid = false;
double lat = 0, lng = 0;
int    sat = 0;
unsigned long lastNmea = 0, lastGpsDebug = 0;

// ========================================================
// SERVO + STEPPER
// ========================================================
Servo servoTilting;
int   posisiSekarang = 0;
unsigned long waktuAktuator = 0;

const long    SENSOR_INTERVAL = 5000;
unsigned long lastPublish     = 0;

// ========================================================
// INA219
// ========================================================
Adafruit_INA219 ina219;
bool  sensorOkIna219 = false;

const float BATT_MAX_V        = 12.6f;
const float BATT_MIN_V        =  9.0f;
const float BATT_WARN_V       = 10.2f;
const float BATT_CRIT_V       =  9.6f;
const float BATT_CAPACITY_MAH = 6800.0f;

float mAhUsed = 0.0f;
unsigned long lastMahCalc = 0;
float battVoltage = 0, battCurrent_mA = 0, battPower_mW = 0, battPercent = 0;

// ========================================================
// HX711
// ========================================================
HX711_ADC scale(HX711_DT, HX711_SCK);
float calibration_factor = 40.24f;
float berat = 0;
bool  scaleReady = false;

// ========================================================
// HELPER
// ========================================================
float hitungPersen(float jarak, float kosong, bool kalibOk) {
  if (jarak < 0) return -1;
  float p;
  if (kalibOk && kosong > 0)
    p = (1.0f - (jarak / kosong)) * 100.0f;
  else {
    if      (jarak <= 37.0f)  p = 100.0f;
    else if (jarak >= 68.75f) p = 0.0f;
    else p = ((68.75f - jarak) / (68.75f - 37.0f)) * 100.0f;
  }
  return constrain(p, 0.0f, 100.0f);
}

float getBatteryPercent(float voltage) {
  if (voltage >= BATT_MAX_V) return 100.0f;
  if (voltage <= BATT_MIN_V) return   0.0f;
  const float vT[] = { 9.0f, 9.6f, 10.2f, 10.8f, 11.4f, 12.0f, 12.6f };
  const float pT[] = { 0.0f, 5.0f, 15.0f, 35.0f, 60.0f, 85.0f, 100.0f };
  for (int i = 0; i < 6; i++) {
    if (voltage >= vT[i] && voltage <= vT[i+1]) {
      float r = (voltage - vT[i]) / (vT[i+1] - vT[i]);
      return pT[i] + r * (pT[i+1] - pT[i]);
    }
  }
  return 0.0f;
}

// ========================================================
// STEPPER
// ========================================================
// Catatan: ENA dilepas setiap gerakan selesai, sehingga piringan bebas berputar
// selama tilting() berjalan. Bila piringan tergeser pada jendela itu, nilai
// posisiSekarang tidak lagi mewakili posisi fisik dan keDerajat(0) — termasuk
// perintah "reset" — akan mendarat meleset. Sistem ini open-loop; koreksi posisi
// hanya mungkin bila dipasang limit switch atau sensor home.
void bergerak(bool dir, int steps) {
  digitalWrite(ENA_PIN, LOW);
  digitalWrite(DIR_PIN, dir);
  for (int i = 0; i < steps; i++) {
    digitalWrite(STEP_PIN, HIGH); delayMicroseconds(500);
    digitalWrite(STEP_PIN, LOW);  delayMicroseconds(500);
  }
  digitalWrite(ENA_PIN, HIGH);
}

void keDerajat(int deg) {
  int target = (deg * STPR) / 360;
  int delta  = target - posisiSekarang;
  if (delta != 0) { bergerak(delta > 0, abs(delta)); posisiSekarang = target; }
}

void tilting() {
  servoTilting.write(SERVO_MIRING);
  delay(TILT_DELAY);
  servoTilting.write(SERVO_DATAR);
  delay(500);
}

void prosesKategori(const String& kat) {
  if      (kat == "organik")   { keDerajat(60);  tilting(); }
  else if (kat == "anorganik") { keDerajat(180); tilting(); }
  else if (kat == "B3")        { keDerajat(300); tilting(); }
  else if (kat == "reset")       keDerajat(0);
  waktuAktuator = millis();
}

// ========================================================
// VL53L0X
// ========================================================
void initSensorLaser() {
  Wire.begin();
  Wire.setClock(100000);
  Wire3.begin();
  Wire3.setClock(100000);

  pinMode(XSHUT_ORGANIK,   OUTPUT); digitalWrite(XSHUT_ORGANIK,   LOW);
  pinMode(XSHUT_ANORGANIK, OUTPUT); digitalWrite(XSHUT_ANORGANIK, LOW);
  pinMode(XSHUT_B3,        OUTPUT); digitalWrite(XSHUT_B3,        LOW);
  delay(50);

  digitalWrite(XSHUT_ORGANIK, HIGH); delay(150);
  sensorOkOrganik = loxOrganik.begin(ADDR_ORGANIK, false, &Wire);
  SERIAL_DEBUG.println(sensorOkOrganik ? "[VL53] Organik OK (0x30, I2C1)" : "[VL53] Organik GAGAL");

  digitalWrite(XSHUT_B3, HIGH); delay(150);
  sensorOkB3 = loxB3.begin(ADDR_B3, false, &Wire);
  SERIAL_DEBUG.println(sensorOkB3 ? "[VL53] B3 OK (0x32, I2C1)" : "[VL53] B3 GAGAL");

  digitalWrite(XSHUT_ANORGANIK, HIGH); delay(150);
  sensorOkAnorganik = loxAnorganik.begin(ADDR_ANORGANIK, false, &Wire3);
  SERIAL_DEBUG.println(sensorOkAnorganik ? "[VL53] Anorganik OK (0x31, I2C3)" : "[VL53] Anorganik GAGAL");
}

bool kalibrasiSatu(Adafruit_VL53L0X &sensor, float &kosong, bool &ok, const char* nama) {
  float total = 0; int valid = 0;
  for (int i = 0; i < 10; i++) {
    VL53L0X_RangingMeasurementData_t m;
    sensor.rangingTest(&m, false);
    if (m.RangeStatus != 4) { total += m.RangeMilliMeter / 10.0f; valid++; }
    delay(100);
  }
  if (valid >= 5) {
    kosong = total / valid; ok = true;
    SERIAL_DEBUG.printf("[Kalib] %s OK -> %.1f cm\n", nama, kosong);
    return true;
  }
  ok = false; SERIAL_DEBUG.printf("[Kalib] %s GAGAL\n", nama);
  return false;
}

bool kalibrasiSemua() {
  bool r = true;
  if (sensorOkOrganik)   r &= kalibrasiSatu(loxOrganik,   jarakKosongOrganik,   kalibrasiOkOrganik,   "Organik");
  if (sensorOkAnorganik) r &= kalibrasiSatu(loxAnorganik, jarakKosongAnorganik, kalibrasiOkAnorganik, "Anorganik");
  if (sensorOkB3)        r &= kalibrasiSatu(loxB3,        jarakKosongB3,        kalibrasiOkB3,        "B3");
  return r;
}

void bacaJarak() {
  VL53L0X_RangingMeasurementData_t m;
  if (sensorOkOrganik)   { loxOrganik.rangingTest(&m, false);
    jarakOrganik   = (m.RangeStatus != 4) ? m.RangeMilliMeter / 10.0f : -1; }
  if (sensorOkAnorganik) { loxAnorganik.rangingTest(&m, false);
    jarakAnorganik = (m.RangeStatus != 4) ? m.RangeMilliMeter / 10.0f : -1; }
  if (sensorOkB3)        { loxB3.rangingTest(&m, false);
    jarakB3        = (m.RangeStatus != 4) ? m.RangeMilliMeter / 10.0f : -1; }

  float pO = hitungPersen(jarakOrganik,   jarakKosongOrganik,   kalibrasiOkOrganik);
  float pA = hitungPersen(jarakAnorganik, jarakKosongAnorganik, kalibrasiOkAnorganik);
  float pB = hitungPersen(jarakB3,        jarakKosongB3,        kalibrasiOkB3);
  SERIAL_DEBUG.printf("[Jarak] O:%.1fcm(%.0f%%) A:%.1fcm(%.0f%%) B3:%.1fcm(%.0f%%)\n",
    jarakOrganik   >= 0 ? jarakOrganik   : 0, pO >= 0 ? pO : 0,
    jarakAnorganik >= 0 ? jarakAnorganik : 0, pA >= 0 ? pA : 0,
    jarakB3        >= 0 ? jarakB3        : 0, pB >= 0 ? pB : 0);
}

// ========================================================
// INA219
// ========================================================
void initBaterai() {
  sensorOkIna219 = ina219.begin(&Wire3);
  if (!sensorOkIna219) {
    SERIAL_DEBUG.println("[INA219] GAGAL! Cek wiring.");
    return;
  }
  ina219.setCalibration_16V_400mA();
  delay(200);
  float initV   = ina219.getBusVoltage_V();
  float initPct = getBatteryPercent(initV);
  mAhUsed = BATT_CAPACITY_MAH * (1.0f - (initPct / 100.0f));
  lastMahCalc = millis();
  SERIAL_DEBUG.println("[INA219] OK");
  SERIAL_DEBUG.printf("[INA219] Boot: %.3fV | %.1f%%\n", initV, initPct);
}

void bacaBaterai() {
  if (!sensorOkIna219) return;
  unsigned long now = millis();
  float currentMA = ina219.getCurrent_mA();
  unsigned long dt = now - lastMahCalc;
  if (dt >= 200) {
    if (currentMA > 0) mAhUsed += currentMA * (dt / 3600000.0f);
    lastMahCalc = now;
  }
  float shuntV   = ina219.getShuntVoltage_mV();
  float busV     = ina219.getBusVoltage_V();
  battVoltage    = busV + (shuntV / 1000.0f);
  battCurrent_mA = currentMA;
  battPower_mW   = ina219.getPower_mW();
  battPercent    = getBatteryPercent(battVoltage);
  if      (battVoltage <= BATT_CRIT_V) SERIAL_DEBUG.println("[Baterai] *** KRITIS! ***");
  else if (battVoltage <= BATT_WARN_V) SERIAL_DEBUG.println("[Baterai] ** Hampir habis **");
}

// ========================================================
// HX711
// ========================================================
void initLoadCell() {
  scale.begin();
  scale.start(2000);
  if (scale.getTareTimeoutFlag()) {
    SERIAL_DEBUG.println("[HX711] GAGAL — cek wiring DT/SCK");
    scaleReady = false;
    return;
  }
  scale.setCalFactor(calibration_factor);
  scaleReady = true;
  SERIAL_DEBUG.println("[HX711] OK — tare selesai");
}

void bacaBerat() {
  if (!scaleReady) return;
  if (scale.update()) {
    berat = scale.getData();
    if (abs(berat) < 2.0f) berat = 0;
  }
}

// ========================================================
// GPS
// ========================================================
// Pembacaan GPS dibatasi jumlah byte dan waktu. Tanpa batas, modul GPS yang
// kehilangan daya membuat pin TX mengambang dan menghasilkan noise terus-menerus,
// sehingga available() tidak pernah kosong dan seluruh loop() berhenti di sini —
// telemetri tidak terkirim dan perintah dari Raspberry Pi tidak diproses.
// Kondisi ini sulit dikenali dari luar karena USB CDC dilayani interrupt,
// sehingga port /dev/ttyACM* tetap muncul walau loop utama sudah berhenti.
void bacaGps() {
  unsigned long mulai = millis();
  int n = 0;
  while (SERIAL_GPS.available() && n < 512 && (millis() - mulai) < 20) {
    gps.encode(SERIAL_GPS.read());
    lastNmea = millis();
    n++;
  }
  if (gps.location.isValid()) {
    lokasiValid = true;
    lat = gps.location.lat();
    lng = gps.location.lng();
    sat = gps.satellites.value();
  }
  if (millis() - lastGpsDebug >= 5000) {
    lastGpsDebug = millis();
    if (millis() - lastNmea > 3000)
      SERIAL_DEBUG.println("[GPS] UART MATI");
    else if (!lokasiValid)
      SERIAL_DEBUG.printf("[GPS] Belum fix | Sat:%d | Chars:%lu\n", sat, gps.charsProcessed());
    else
      SERIAL_DEBUG.printf("[GPS] Fix: %.6f, %.6f | Sat:%d\n", lat, lng, sat);
  }
}

// ========================================================
// KIRIM KE RASPI BRIDGE
// ========================================================
void kirimKeRaspi() {
  float pO = hitungPersen(jarakOrganik,   jarakKosongOrganik,   kalibrasiOkOrganik);
  float pA = hitungPersen(jarakAnorganik, jarakKosongAnorganik, kalibrasiOkAnorganik);
  float pB = hitungPersen(jarakB3,        jarakKosongB3,        kalibrasiOkB3);

  StaticJsonDocument<640> doc;
  doc["organik"]["distance"]   = jarakOrganik   >= 0 ? jarakOrganik   : 0;
  doc["organik"]["volume"]     = pO >= 0 ? round(pO) : 0;
  doc["anorganik"]["distance"] = jarakAnorganik >= 0 ? jarakAnorganik : 0;
  doc["anorganik"]["volume"]   = pA >= 0 ? round(pA) : 0;
  doc["b3"]["distance"]        = jarakB3        >= 0 ? jarakB3        : 0;
  doc["b3"]["volume"]          = pB >= 0 ? round(pB) : 0;
  doc["lat"]                   = lokasiValid ? lat : 0;
  doc["lng"]                   = lokasiValid ? lng : 0;
  doc["sat"]                   = sat;
  doc["battery"]["voltage"]    = round(battVoltage    * 100) / 100.0;
  doc["battery"]["current_mA"] = round(battCurrent_mA * 100) / 100.0;
  doc["battery"]["power_mW"]   = round(battPower_mW   * 100) / 100.0;
  doc["battery"]["percent"]    = round(battPercent);
  doc["battery"]["ok"]         = sensorOkIna219;
  doc["berat_g"]               = round(berat * 10) / 10.0;
  doc["scale_ok"]              = scaleReady;

  serializeJson(doc, SERIAL_ESP);
  SERIAL_ESP.println();

  SERIAL_DEBUG.print("[->Raspi] ");
  serializeJson(doc, SERIAL_DEBUG);
  SERIAL_DEBUG.println();
}

// ========================================================
// SETUP
// ========================================================
void setup() {
  SERIAL_DEBUG.begin(115200);
  delay(2000);
  SERIAL_DEBUG.println("===== STM32F405 SmartBin =====");

  pinMode(STEP_PIN, OUTPUT); pinMode(DIR_PIN, OUTPUT); pinMode(ENA_PIN, OUTPUT);
  digitalWrite(ENA_PIN, HIGH);

  servoTilting.attach(SERVO_PIN);
  servoTilting.write(SERVO_DATAR);

  SERIAL_GPS.begin(38400);
  lastNmea = lastGpsDebug = millis();

  SERIAL_ESP.begin(115200);

  initSensorLaser();
  initBaterai();
  initLoadCell();  // HX711 — non-blocking timeout 2s

  SERIAL_DEBUG.println("[Kalib] Pastikan tong KOSONG — mulai 3 detik...");
  delay(3000);
  int retry = 0;
  while (!kalibrasiSemua() && retry++ < 5) delay(2000);
  SERIAL_DEBUG.println("[Sistem] RUNNING!");
}

// ========================================================
// LOOP
// ========================================================
void loop() {
#if DEBUG_TIMING
  unsigned long t1 = millis(); bacaGps();
  unsigned long t2 = millis(); bacaJarak();
  unsigned long t3 = millis(); bacaBaterai();
  unsigned long t4 = millis(); bacaBerat();
  SERIAL_DEBUG.printf("[Waktu] gps:%lu jarak:%lu batt:%lu berat:%lu\n",
                      t2 - t1, t3 - t2, t4 - t3, millis() - t4);
#else
  bacaGps();
  bacaJarak();
  bacaBaterai();
  bacaBerat();   // non-blocking, update internal buffer HX711
#endif

  if (posisiSekarang != 0 && millis() - waktuAktuator >= 3000) {
    keDerajat(0);
    SERIAL_DEBUG.println("[Aktuator] Auto-reset -> 0 derajat");
  }

  if (millis() - lastPublish >= SENSOR_INTERVAL) {
    kirimKeRaspi();
    lastPublish = millis();
  }

  if (SERIAL_DEBUG.available()) {
    String cmd = SERIAL_DEBUG.readStringUntil('\n');
    cmd.trim();
    if      (cmd == "jarak")  bacaJarak();
    else if (cmd == "lokasi") SERIAL_DEBUG.printf("[GPS] %.6f, %.6f | Sat:%d\n", lat, lng, sat);
    else if (cmd == "kalib")  kalibrasiSemua();
    else if (cmd == "batt")   SERIAL_DEBUG.printf("[Baterai] %.3fV | %.2fmA | %.1f%%\n", battVoltage, battCurrent_mA, battPercent);
    else if (cmd == "berat")  SERIAL_DEBUG.printf("[HX711] %.2f g | faktor: %.2f\n", berat, calibration_factor);
    else if (cmd == "tare")  { scale.tareNoDelay(); SERIAL_DEBUG.println("[HX711] Tare..."); }
    else if (cmd == "cal+")  { calibration_factor += 10; scale.setCalFactor(calibration_factor); SERIAL_DEBUG.printf("[HX711] Faktor: %.2f\n", calibration_factor); }
    else if (cmd == "cal-")  { calibration_factor -= 10; scale.setCalFactor(calibration_factor); SERIAL_DEBUG.printf("[HX711] Faktor: %.2f\n", calibration_factor); }
    else                      prosesKategori(cmd);
  }

  delay(200);
}
