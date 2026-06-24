/* ============================================================
   SmartBin — Pemilah Sampah (ESP32 + 1 Servo 20kg, 3 posisi)
   ------------------------------------------------------------
   Cara kerja:
     Raspi (main.py)  --USB serial-->  ESP32 (sketch ini)
     main.py mengirim teks: "organik\n" / "anorganik\n" / "B3\n" / "reset\n"
     ESP32 baca -> putar servo ke sudut kategori -> tunggu sampah
     jatuh -> balik ke posisi NETRAL.

   Servo: high-torque 20kg (DS3218 / MG996R-class), kontrol PWM standar.

   >>> PENTING soal DAYA <<<
   Servo 20kg menarik arus besar (bisa >1A saat bergerak/menahan).
   JANGAN ambil daya dari pin 5V ESP32 — ESP32 bisa nge-reset/brownout.
   Pakai catu daya terpisah 5–6V (mis. UBEC/adaptor 5V 3A):
     - Servo VCC (merah)  -> + catu daya 5–6V
     - Servo GND (coklat) -> - catu daya  DAN  GND ESP32 (GND HARUS digabung!)
     - Servo sinyal (oranye/kuning) -> GPIO 13 ESP32

   Library: pasang "ESP32Servo" via Arduino Library Manager.
   Baud   : 115200 (samakan dengan SERIAL_BAUD di main.py).
   ============================================================ */

#include <ESP32Servo.h>

// ---------- KONFIGURASI ----------
const int SERVO_PIN = 13;     // GPIO sinyal servo

// Sudut tiap kategori (sesuaikan dgn mekanik corong/flap kamu)
const int SUDUT_NETRAL    = 90;   // posisi diam (corong di tengah)
const int SUDUT_ORGANIK   = 0;    // miring ke laci Organik
const int SUDUT_ANORGANIK = 90;   // tengah/laci Anorganik  (ubah jika perlu)
const int SUDUT_B3        = 180;  // miring ke laci B3

const int WAKTU_JATUH_MS  = 1500; // tunggu sampah meluncur sebelum balik netral
// ---------------------------------

Servo servo;

void gerakKe(int sudut) {
  servo.write(sudut);
}

void pilah(const String& kategori, int sudut) {
  Serial.print("[ESP32] Pilah: ");
  Serial.print(kategori);
  Serial.print(" -> sudut ");
  Serial.println(sudut);

  gerakKe(sudut);            // buka ke kategori
  delay(WAKTU_JATUH_MS);     // tunggu sampah jatuh
  gerakKe(SUDUT_NETRAL);     // balik ke posisi netral
  Serial.println("[ESP32] Selesai, kembali NETRAL");
}

void setup() {
  Serial.begin(115200);
  delay(300);

  // Rentang pulsa lebar supaya servo besar bisa capai 0–180 penuh
  servo.setPeriodHertz(50);            // 50 Hz standar servo
  servo.attach(SERVO_PIN, 500, 2500);  // min/max pulse (us)

  gerakKe(SUDUT_NETRAL);
  Serial.println("[ESP32] Pemilah servo siap. Perintah: organik | anorganik | B3 | reset");
}

void loop() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toLowerCase();   // samakan: "B3" -> "b3"

    if (cmd == "organik") {
      pilah("Organik", SUDUT_ORGANIK);
    } else if (cmd == "anorganik") {
      pilah("Anorganik", SUDUT_ANORGANIK);
    } else if (cmd == "b3") {
      pilah("B3", SUDUT_B3);
    } else if (cmd == "reset") {
      gerakKe(SUDUT_NETRAL);
      Serial.println("[ESP32] Reset -> NETRAL");
    } else if (cmd.length() > 0) {
      Serial.print("[ESP32] Perintah tidak dikenal: ");
      Serial.println(cmd);
    }
  }
}
