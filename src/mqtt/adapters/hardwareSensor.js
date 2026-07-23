/**
 * Adapter payload sensor dari perangkat SmartBin EcoSort (STM32 → Raspberry Pi).
 *
 * Hardware kirim payload BERTINGKAT dengan 3 kompartemen:
 *   {
 *     "organik":   { "distance": 14.9, "volume": 82 },
 *     "anorganik": { "distance": 19.5, "volume": 9  },
 *     "b3":        { "distance": 819.1,"volume": 0  },
 *     "lat": -6.98, "lng": 107.62, "sat": 0,
 *     "battery": { "voltage": 11.03, "current_mA": 0.1,
 *                  "power_mW": 2, "percent": 45, "ok": true },
 *     "timestamp": 1720598412.345
 *   }
 *
 * Backend (SensorLog) masih menyimpan SATU nilai volume/distance per tong, jadi
 * payload di-FLATTEN dulu: kompartemen paling penuh dipakai sebagai volume tong.
 * Data per-kompartemen tetap dikembalikan terpisah untuk dashboard (WebSocket).
 *
 * Payload flat versi lama (ESP32) dilewatkan apa adanya.
 */

/** Nama kompartemen sesuai dokumentasi hardware. */
export const COMPARTMENTS = ['organik', 'anorganik', 'b3'];

/**
 * Jarak >= nilai ini = VL53L0X out-of-range (RangeStatus=4, tipikal 819.1 cm).
 * Bukan jarak valid → jangan disimpan/ditampilkan, tong dianggap kosong.
 */
export const OOR_DISTANCE_CM = 800;

/** Tegangan pack 3S Li-Ion: penuh (12.6V) dan kosong (9.0V). */
export const BATT_FULL_V = 12.6;
export const BATT_EMPTY_V = 9.0;

/**
 * Konversi tegangan pack 3S Li-Ion → persen baterai (0–100).
 * Perkiraan linear pada rentang {@link BATT_EMPTY_V}–{@link BATT_FULL_V}.
 * @param {any} v tegangan (Volt)
 * @returns {number|null} persen 0–100, atau null kalau tegangan tidak valid (≤ 0)
 */
export function voltageToBatteryPercent(v) {
    if (typeof v !== 'number' || v <= 0) return null;
    const pct = ((v - BATT_EMPTY_V) / (BATT_FULL_V - BATT_EMPTY_V)) * 100;
    return Math.round(Math.max(0, Math.min(100, pct)));
}

/** @param {any} raw */
export function isHardwarePayload(raw) {
    if (!raw || typeof raw !== 'object') return false;
    if (raw.battery && typeof raw.battery === 'object') return true;
    return COMPARTMENTS.some((c) => raw[c] && typeof raw[c] === 'object');
}

/**
 * Ubah payload hardware jadi bentuk flat yang dimengerti handler sensor.
 *
 * @param {object} raw payload mentah dari MQTT
 * @returns {{ payload: object, compartments: object|null }}
 *   payload      → bentuk flat (weight/volume/battery/distance/lat/lng/rssi)
 *   compartments → { organik: {distance, volume}, ... } atau null kalau bukan payload hardware
 */
export function normalizeSensorPayload(raw) {
    if (!isHardwarePayload(raw)) return { payload: raw, compartments: null };

    const compartments = {};
    for (const name of COMPARTMENTS) {
        const c = raw[name];
        if (!c || typeof c !== 'object') continue;

        // OOR → jarak tidak valid, tong kosong (lihat OOR_DISTANCE_CM).
        const oor = typeof c.distance === 'number' && c.distance >= OOR_DISTANCE_CM;
        compartments[name] = {
            distance: oor ? null : (typeof c.distance === 'number' ? c.distance : null),
            volume: oor ? 0 : (typeof c.volume === 'number' ? c.volume : 0),
        };
    }

    // Kompartemen paling penuh mewakili volume tong (satu kolom di sensor_logs).
    let fullest = null;
    for (const name of Object.keys(compartments)) {
        if (!fullest || compartments[name].volume > compartments[fullest].volume) fullest = name;
    }

    /** @type {Record<string, any>} */
    const payload = {};

    if (fullest) {
        payload.volume = compartments[fullest].volume;
        // Hanya kirim distance kalau jaraknya valid (bukan OOR).
        if (compartments[fullest].distance != null) {
            payload.distance = compartments[fullest].distance;
        }
    }

    // battery.ok = false → INA219 tidak terdeteksi; jangan alert/tampilkan baterai palsu.
    if (raw.battery && typeof raw.battery === 'object' && raw.battery.ok !== false) {
        // Tegangan dipakai untuk alert baterai (ambang dokumen: < 10.2V / < 9.6V).
        if (typeof raw.battery.voltage === 'number') payload.batteryVoltage = raw.battery.voltage;
        // Persen baterai: pakai `percent` firmware kalau valid (>0; kurva Li-Ion lebih
        // akurat). Kalau firmware belum menghitung (0/kosong), turunkan dari tegangan.
        const pct = (typeof raw.battery.percent === 'number' && raw.battery.percent > 0)
            ? raw.battery.percent
            : voltageToBatteryPercent(raw.battery.voltage);
        if (pct != null) payload.battery = pct;
    }

    // GPS hanya dipakai kalau sudah fix (sat > 0) dan koordinatnya bukan 0,0.
    const hasFix = typeof raw.sat === 'number' && raw.sat > 0;
    const hasCoords =
        typeof raw.lat === 'number' && typeof raw.lng === 'number' &&
        !(raw.lat === 0 && raw.lng === 0);
    if (hasFix && hasCoords) {
        payload.lat = raw.lat;
        payload.lng = raw.lng;
    }

    // Load cell (HX711): hardware kirim berat dalam GRAM (`berat_g`); backend
    // menyimpan `weight` dalam KG → bagi 1000. `scale_ok === false` artinya HX711
    // tidak terbaca → beratnya tidak dipakai.
    // Load cell tanpa tare bisa baca NEGATIF → clamp ke 0 (skema butuh weight ≥ 0;
    // kalau tidak, seluruh payload ditolak & sensor log hilang).
    if (raw.scale_ok !== false && typeof raw.berat_g === 'number') {
        payload.weight = Math.max(0, raw.berat_g / 1000);
    }

    // `rssi` tidak ada (Raspi publish via Ethernet/WiFi, bukan ESP32) → dibiarkan kosong.

    return { payload, compartments: Object.keys(compartments).length ? compartments : null };
}
