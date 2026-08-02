#include "DHT.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <TinyGPS++.h>
#include <Wire.h>
#include <Adafruit_VL53L0X.h>
#include <HX711_ADC.h>

//======================================
// WIFI CONFIG
//======================================
const char* ssid     = "Kostbudewibawah";
const char* password = "Kostbudewi02";

//======================================
// HTTP API CONFIG (SmartBin Server)
//======================================
const char* server_endpoint = "https://smartbin.sbs/ingest/sensor";
const char* device_key      = "4cc3a6eb99c5bb859b5006b0d23d8f0bd41294953371d543";
const char* node_id         = "bin-002";

//======================================
// MQTT CONFIG (TLS - PORT 8883)
//======================================
const char* mqtt_server   = "2f77e302643340f8b63d38c051743935.s1.eu.hivemq.cloud";
const int   mqtt_port     = 8883;
const char* mqtt_user     = "bintrash";
const char* mqtt_password = "Smartbrinbin1";
const char* mqtt_client_id = "ESP32_SmartBin";

const char* topic_smartbin_data = "smartbin/data";

WiFiClientSecure espClient;
PubSubClient client(espClient);

#define MQTT_ENABLED true

//======================================
// LORA (board LoRa32 via UART1)
//======================================
#define LORA_LINK_TX   32
#define LORA_LINK_RX   33
#define LORA_LINK_BAUD 115200
HardwareSerial LoRaSerial(1);
uint32_t seqCounter = 0;

//======================================
// DHT22
//======================================
#define DHT_PIN 4
#define DHT_TYPE DHT22
DHT dht(DHT_PIN, DHT_TYPE);

//======================================
// MQ135
//======================================
#define MQ135_PIN 34

//======================================
// VL53L0X (I2C)
//======================================
#define VL53_SDA_PIN 21
#define VL53_SCL_PIN 22
Adafruit_VL53L0X vl53 = Adafruit_VL53L0X();
bool vl53_ok = false;

//======================================
// GPS M10-12C (via Serial2 - UART2)
//======================================
#define GPS_RX_PIN 16
#define GPS_TX_PIN 17
#define GPS_BAUD   9600

TinyGPSPlus gps;
HardwareSerial GPSSerial(2);

bool     lokasiValid   = false;
double   lat           = 0.0;
double   lng           = 0.0;
uint32_t sat           = 0;
unsigned long lastNmea      = 0;
unsigned long lastGpsDebug  = 0;

unsigned long lastPublishTime = 0;
const unsigned long PUBLISH_INTERVAL = 2000; // 2 detik

//======================================
// HX711 (Load Cell)
//======================================
#define HX711_DT_PIN   26
#define HX711_SCK_PIN  27

HX711_ADC scale(HX711_DT_PIN, HX711_SCK_PIN);
float calibration_factor = 40.24f;
bool  scaleReady = false;
float berat = 0;

//======================================
// KALIBRASI TONG (fallback)
//======================================
#define TINGGI_TONG        77.0
#define BATAS_KOSONG       70.0
#define BATAS_SEDANG       49.0
#define BATAS_HAMPIR_PENUH 24.5

float jarakKosongCm = 0;
bool  kalibrasiOk   = false;

enum SystemState { STATE_CALIBRATING, STATE_RUNNING };
SystemState systemState = STATE_CALIBRATING;
const int MAX_PERCOBAAN_KALIBRASI = 5;
int     percobaanKalibrasi = 0;

//======================================
// WIFI CONNECT
//======================================
void connectWiFi() {
  Serial.print("Menghubungkan ke WiFi");
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("WiFi Terhubung. IP: ");
  Serial.println(WiFi.localIP());
}

//======================================
// MQTT RECONNECT
//======================================
void reconnectMQTT() {
  while (!client.connected()) {
    Serial.print("Menghubungkan ke MQTT Broker...");

    if (client.connect(mqtt_client_id, mqtt_user, mqtt_password)) {
      Serial.println(" Terhubung!");
    } else {
      Serial.print(" Gagal, rc=");
      Serial.print(client.state());
      Serial.println(" coba lagi dalam 5 detik");
      delay(5000);
    }
  }
}

//======================================
// BACA JARAK MENTAH (VL53L0X)
//======================================
float bacaJarakMentah() {
  if (!vl53_ok) return -1;

  VL53L0X_RangingMeasurementData_t measure;
  vl53.rangingTest(&measure, false);

  if (measure.RangeStatus == 4) return -1;

  return measure.RangeMilliMeter / 10.0;
}

//======================================
// KALIBRASI VL53L0X
//======================================
bool kalibrasiSensorJarak() {
  if (!vl53_ok) return false;

  Serial.println("[Kalibrasi] Pastikan tong KOSONG, mulai dalam 3 detik...");
  delay(3000);

  const int N = 10;
  float total = 0;
  int   valid = 0;

  for (int i = 0; i < N; i++) {
    float jarak = bacaJarakMentah();
    if (jarak >= 0) {
      total += jarak;
      valid++;
    }
    delay(100);
  }

  if (valid >= 5) {
    jarakKosongCm = total / valid;
    kalibrasiOk = true;
    Serial.print("[Kalibrasi] OK, jarak kosong = ");
    Serial.print(jarakKosongCm);
    Serial.println(" cm");
    return true;
  } else {
    kalibrasiOk = false;
    Serial.println("[Kalibrasi] GAGAL, sensor tidak stabil.");
    return false;
  }
}

//======================================
// HITUNG PERSEN TERISI
//======================================
float hitungPersenTerisi(float jarak_cm) {
  if (jarak_cm < 0) return -1;

  float persen;
  if (kalibrasiOk) {
    persen = (1.0 - (jarak_cm / jarakKosongCm)) * 100.0;
  } else {
    if      (jarak_cm <= BATAS_HAMPIR_PENUH) persen = 100;
    else if (jarak_cm >= BATAS_KOSONG)       persen = 0;
    else persen = ((BATAS_KOSONG - jarak_cm) / (BATAS_KOSONG - BATAS_HAMPIR_PENUH)) * 100.0;
  }
  return constrain(persen, 0, 100);
}

String labelDariPersen(float persen) {
  if (persen < 0)   return "SENSOR ERROR";
  if (persen < 25)  return "KOSONG";
  if (persen < 50)  return "SEDANG";
  if (persen < 75)  return "HAMPIR PENUH";
  return "PENUH";
}

//======================================
// BACA GPS
//======================================
void bacaGps() {
  while (GPSSerial.available()) {
    gps.encode(GPSSerial.read());
    lastNmea = millis();
  }

  if (gps.location.isValid()) {
    lokasiValid = true;
    lat = gps.location.lat();
    lng = gps.location.lng();
    sat = gps.satellites.value();
  }

  if (millis() - lastGpsDebug >= 5000) {
    lastGpsDebug = millis();
    if (millis() - lastNmea > 3000) {
      Serial.println("[GPS] UART MATI");
      lokasiValid = false;
    }
  }
}

//======================================
// HX711
//======================================
void initLoadCell() {
  scale.begin();
  scale.start(2000);
  if (scale.getTareTimeoutFlag()) {
    Serial.println("[HX711] GAGAL — cek wiring DT/SCK");
    scaleReady = false;
    return;
  }
  scale.setCalFactor(calibration_factor);
  scaleReady = true;
  Serial.println("[HX711] OK — tare selesai");
}

void bacaBerat() {
  if (!scaleReady) return;
  if (scale.update()) {
    berat = scale.getData();
    if (abs(berat) < 2.0f) berat = 0;
  }
}

//======================================
// KIRIM DATA KE HTTP ENDPOINT (SMARTBIN.SBS)
//======================================
void sendDataToServer(uint32_t seq, float weight, float volume, int gasVal) {
  if (WiFi.status() == WL_CONNECTED) {
    WiFiClientSecure secureClient;
    secureClient.setInsecure();

    HTTPClient http;
    http.begin(secureClient, server_endpoint);

    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Key", device_key);

    char httpJsonBuf[220];
    snprintf(httpJsonBuf, sizeof(httpJsonBuf),
      "{\"nodeId\":\"%s\",\"seq\":%lu,\"transport\":\"http\",\"weight\":%.2f,\"volume\":%.1f,\"battery\":90,\"gas\":%d}",
      node_id, (unsigned long)seq, weight, volume, gasVal);

    int httpResponseCode = http.POST(httpJsonBuf);

    if (httpResponseCode > 0) {
      Serial.print("[HTTP] Response code: ");
      Serial.println(httpResponseCode);
    } else {
      Serial.print("[HTTP] Error on sending POST: ");
      Serial.println(http.errorToString(httpResponseCode).c_str());
    }

    http.end();
  }
}

//======================================
// KIRIM DATA KE LORA (board LoRa32)
//======================================
void sendDataToLoRa(uint32_t seq, float weight, float volume, int gasVal) {
  char buf[140];
  snprintf(buf, sizeof(buf),
    "{\"nodeId\":\"%s\",\"seq\":%lu,\"weight\":%.2f,\"volume\":%.1f,\"battery\":90,\"gas\":%d}",
    node_id, (unsigned long)seq, weight, volume, gasVal);

  LoRaSerial.println(buf);
  Serial.print("[LoRa] Kirim: ");
  Serial.println(buf);
}

//======================================
// SETUP
//======================================
void setup() {
  Serial.begin(115200);

  LoRaSerial.begin(LORA_LINK_BAUD, SERIAL_8N1, LORA_LINK_RX, LORA_LINK_TX);

  dht.begin();
  Wire.begin(VL53_SDA_PIN, VL53_SCL_PIN);

  if (!vl53.begin()) {
    Serial.println("Gagal mendeteksi VL53L0X. Cek wiring!");
    vl53_ok = false;
  } else {
    Serial.println("VL53L0X siap.");
    vl53_ok = true;
  }

  GPSSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  initLoadCell();
  delay(2000);

  connectWiFi();

#if MQTT_ENABLED
  espClient.setInsecure();
  client.setServer(mqtt_server, mqtt_port);
#endif

  Serial.println("===== TAHAP KALIBRASI =====");
  while (systemState == STATE_CALIBRATING) {
    percobaanKalibrasi++;
    bool sukses = kalibrasiSensorJarak();

    if (sukses) {
      systemState = STATE_RUNNING;
    } else if (percobaanKalibrasi >= MAX_PERCOBAAN_KALIBRASI) {
      systemState = STATE_RUNNING;
    } else {
      delay(2000);
    }
  }

  Serial.println("========================================");
  Serial.println("      SMART BIN SYSTEM READY");
  Serial.println("========================================");
}

//======================================
// LOOP
//======================================
void loop() {
  if (WiFi.status() != WL_CONNECTED)
    connectWiFi();

#if MQTT_ENABLED
  if (!client.connected())
    reconnectMQTT();

  client.loop();
#endif

  bacaGps();

  if (millis() - lastPublishTime >= PUBLISH_INTERVAL) {
    lastPublishTime = millis();

    seqCounter++;

    int gas = analogRead(MQ135_PIN);
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature();

    float distance     = bacaJarakMentah();
    float persenTerisi = hitungPersenTerisi(distance);
    String binStatus   = labelDariPersen(persenTerisi);

    bacaBerat();

    bool   gpsValid  = lokasiValid;
    double latitude  = lat;
    double longitude = lng;

    String wasteStatus = "SAMPAH KERING";
    if (!isnan(humidity) && !isnan(temperature)) {
      if (gas >= 1600 && humidity >= 70) wasteStatus = "SAMPAH BASAH + BAU";
      else if (gas >= 1600) wasteStatus = "SAMPAH BERBAU";
      else if (humidity >= 70) wasteStatus = "SAMPAH BASAH";
    }

    // 1. Publish MQTT
    char jsonBuf[300];
    snprintf(jsonBuf, sizeof(jsonBuf),
      "{\"gas\":%d,\"temperature\":%.2f,\"humidity\":%.2f,\"distance_cm\":%.2f,"
      "\"persen_terisi\":%.0f,\"status_tong\":\"%s\",\"jenis_sampah\":\"%s\","
      "\"berat_kg\":%.2f,\"gps\":{\"fix\":%s,\"lat\":%.6f,\"lng\":%.6f,\"sat\":%d}}",
      gas, temperature, humidity, distance,
      persenTerisi, binStatus.c_str(), wasteStatus.c_str(),
      berat, gpsValid ? "true" : "false", latitude, longitude, sat);

#if MQTT_ENABLED
    client.publish(topic_smartbin_data, jsonBuf);
#endif

    float volumeKirim = (persenTerisi < 0) ? 0 : persenTerisi;

    // 2. Kirim data ke Endpoint Server (HTTP)
    sendDataToServer(seqCounter, berat, volumeKirim, gas);

    // 3. Kirim data ke LoRa
    sendDataToLoRa(seqCounter, berat, volumeKirim, gas);

    Serial.println("========================================");
    Serial.print("Seq             : "); Serial.println(seqCounter);
    Serial.print("Berat (kg)      : "); Serial.println(berat);
    Serial.print("Volume (%)      : "); Serial.println(volumeKirim);
    Serial.print("Gas (ppm)       : "); Serial.println(gas);
    Serial.print("Suhu (°C)       : "); Serial.println(temperature);
    Serial.print("Kelembapan (%)  : "); Serial.println(humidity);
    Serial.print("Longitude GPS   : "); Serial.println(longitude, 6);
    Serial.println("========================================");
  }
}
