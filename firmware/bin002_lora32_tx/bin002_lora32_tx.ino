// ============================================================
// LilyGO TTGO LoRa32 V2.1 - LORA A (TRANSMITTER NODE @ bin-002)
// Tugas: Baca JSON dari USB/UART -> Chunking -> TX LoRa -> Wait ACK
// Fitur: Anti-Bootloop + OLED Live Status + Auto Retry ACK!
// ============================================================

#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// 1. PINOUT OLED
#define OLED_SDA    21
#define OLED_SCL    22
#define OLED_RST    -1
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RST);

// 2. PINOUT LORA TTGO V2.1
#define SCK      5
#define MISO    19
#define MOSI    27
#define SS      18
#define RST     14
#define DIO0    26

// LoRa Config (WAJIB SAMA PERSIS DENGAN LORA B)
#define LORA_FREQ      923E6
#define LORA_SF        7
#define LORA_BW        125E3
#define LORA_CR        5
#define LORA_SYNC      0x12
#define TX_POWER       17

#define NODE_ID        "bin-002"
#define CHUNK_SIZE     140       // Aman di bawah batas maksimal LoRa 255 bytes
#define ACK_TIMEOUT_MS 1500      // Tunggu ACK dari LoRa B maksimal 1.5 detik
#define MAX_RETRIES    3         // Kalau ACK gak datang, coba kirim ulang 3x

uint16_t currentSeq = 1000;

void updateOLED(String status, String line1, String line2, String line3);
void sendPayloadToLoRa(String jsonPayload);
bool transmitChunkAndWaitAck(String packetString, uint16_t seq, int idx);

// Checksum CRC16 CCITT
uint16_t crc16(const String &s) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < s.length(); i++) {
    crc ^= (uint8_t)s[i] << 8;
    for (int b = 0; b < 8; b++) {
      crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1);
    }
  }
  return crc;
}

void setup() {
  Serial.begin(115200);
  delay(500); // Anti-Bootloop!

  Wire.begin(OLED_SDA, OLED_SCL);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    display.begin(SSD1306_SWITCHCAPVCC, 0x3D);
  }
  display.clearDisplay();
  display.display();

  updateOLED("BOOTING...", "APTRG LoRa A (TX)", "Role: TRANSMITTER", "Init Hardware...");

  pinMode(RST, OUTPUT);
  digitalWrite(RST, LOW); delay(20);
  digitalWrite(RST, HIGH); delay(50);

  SPI.begin(SCK, MISO, MOSI, SS);
  LoRa.setPins(SS, RST, DIO0);

  if (!LoRa.begin(LORA_FREQ)) {
    updateOLED("FATAL ERROR", "LoRa RF Failed!", "Cek Hardware!", "Halting...");
    while (1) { delay(500); }
  }

  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setSyncWord(LORA_SYNC);
  LoRa.setTxPower(TX_POWER);

  updateOLED("TX READY!", "Freq: 923 MHz", "Status: STANDBY", "Waiting USB...");
  Serial.println("[OK] LoRa A (Transmitter) Siap! Kirim data JSON lewat Serial USB!");
}

void loop() {
  // Dengerin apakah ESP32 (kabel UART) ngirim data JSON ke kita
  if (Serial.available() > 0) {
    String incomingUsb = Serial.readStringUntil('\n');
    incomingUsb.trim();

    if (incomingUsb.length() > 0) {
      Serial.println("[USB IN] Memproses data: " + incomingUsb.substring(0, 50) + "...");
      updateOLED("TRANSMITTING", "From: ESP32 UART", "Len: " + String(incomingUsb.length()) + "B", "Chunking...");

      // Terbangkan ke LoRa B!
      sendPayloadToLoRa(incomingUsb);
    }
  }
}

void sendPayloadToLoRa(String jsonPayload) {
  currentSeq++;
  if (currentSeq > 9000) currentSeq = 1000;

  int totalLen = jsonPayload.length();
  int totalChunks = (totalLen + CHUNK_SIZE - 1) / CHUNK_SIZE;

  Serial.println("[TX START] Seq: " + String(currentSeq) + " | Total Chunks: " + String(totalChunks));

  for (int i = 0; i < totalChunks; i++) {
    String chunkData = jsonPayload.substring(i * CHUNK_SIZE, min((i + 1) * CHUNK_SIZE, totalLen));

    char crcHex[6];
    sprintf(crcHex, "%04X", crc16(chunkData));

    // Format Sakti: D|<node_id>|<seq>|<idx>/<total>|<payload>|<crc16hex>
    String packetString = "D|" + String(NODE_ID) + "|" + String(currentSeq) + "|" +
                          String(i) + "/" + String(totalChunks) + "|" + chunkData + "|" + String(crcHex);

    bool ackSuccess = false;
    for (int retry = 1; retry <= MAX_RETRIES; retry++) {
      updateOLED("SENDING RF...", "Chk: " + String(i+1) + "/" + String(totalChunks), "Try: " + String(retry) + "/" + String(MAX_RETRIES), "Wait ACK...");

      if (transmitChunkAndWaitAck(packetString, currentSeq, i)) {
        ackSuccess = true;
        Serial.println("[CHK OK] Chunk " + String(i+1) + "/" + String(totalChunks) + " Sukses terkirim & dapat ACK!");
        break; // Lanjut ke chunk berikutnya
      } else {
        Serial.println("[CHK WARN] Chunk " + String(i+1) + " Timeout / No ACK! Re-transmitting...");
        delay(200); // Jeda sebelum retry
      }
    }

    if (!ackSuccess) {
      Serial.println("[FATAL TX] Gagal ngirim chunk setelah " + String(MAX_RETRIES) + "x percobaan! Aborting.");
      updateOLED("TX FAILED!", "No ACK from Core", "Seq: " + String(currentSeq), "Drop Payload!");
      return;
    }
  }

  updateOLED("TX SUCCESS!", "All Chunks Sent", "Seq: " + String(currentSeq), "Back to Standby");
  Serial.println("[TX COMPLETE] Seluruh payload JSON berhasil dipancarkan ke LoRa Core!");
}

bool transmitChunkAndWaitAck(String packetString, uint16_t seq, int idx) {
  // 1. Pancarkan ke udara
  LoRa.beginPacket();
  LoRa.print(packetString);
  LoRa.endPacket();

  // 2. Langsung pindah mode telinga buat dengerin balasan ACK dari LoRa B
  LoRa.receive();

  // 3. Tunggu ACK sampai batas waktu habis
  unsigned long startWait = millis();
  String expectedAckPrefix = "A|" + String(NODE_ID) + "|" + String(seq) + "|" + String(idx);

  while (millis() - startWait < ACK_TIMEOUT_MS) {
    int packetSize = LoRa.parsePacket();
    if (packetSize > 0) {
      String ackIn = "";
      while (LoRa.available()) ackIn += (char)LoRa.read();
      ackIn.trim();

      // Kalau balasan dari LoRa B cocok sama yang kita minta
      if (ackIn.startsWith(expectedAckPrefix)) {
        LoRa.idle(); // Matikan mode RX sementara
        return true;
      }
    }
    delay(5);
  }

  LoRa.idle(); // Matikan mode RX kalau timeout
  return false;
}

void updateOLED(String status, String line1, String line2, String line3) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.print("= " + status + " =");
  display.drawLine(0, 10, SCREEN_WIDTH, 10, SSD1306_WHITE);

  display.setCursor(0, 16); display.println(line1);
  display.setCursor(0, 30); display.println(line2);
  display.setCursor(0, 44); display.println(line3);
  display.display();
}
