import dotenv from 'dotenv';
dotenv.config();

const required = [
  'DATABASE_URL',
  'REDIS_URL',
  'MQTT_BROKER_URL',
  'JWT_SECRET',
  'PORT',
];

for (const key of required) {
  if (!process.env[key]) {
    console.error(`[ENV] Missing required environment variable: ${key}`);
    process.exit(1);
  }
}

export const env = {
  PORT: parseInt(process.env.PORT, 10) || 3000,
  NODE_ENV: process.env.NODE_ENV || 'development',

  DATABASE_URL: process.env.DATABASE_URL,

  REDIS_URL: process.env.REDIS_URL,

  MQTT_BROKER_URL: process.env.MQTT_BROKER_URL,
  MQTT_USERNAME: process.env.MQTT_USERNAME || '',
  MQTT_PASSWORD: process.env.MQTT_PASSWORD || '',
  MQTT_CLIENT_ID: process.env.MQTT_CLIENT_ID || `smartbin-backend-${Date.now()}`,

  JWT_SECRET: process.env.JWT_SECRET,
  JWT_EXPIRES_IN: process.env.JWT_EXPIRES_IN || '7d',

  FIREBASE_CREDENTIALS_PATH: process.env.FIREBASE_CREDENTIALS_PATH || '',

  CLASSIFY_SERVICE_URL: process.env.CLASSIFY_SERVICE_URL || 'http://localhost:8000',

  // Shared secret untuk device (raspi) push hasil klasifikasi via HTTP.
  // Kosong = endpoint POST /classifications terbuka (hanya untuk dev lokal).
  DEVICE_INGEST_KEY: process.env.DEVICE_INGEST_KEY || '',

  CORS_ORIGIN: process.env.CORS_ORIGIN || '',

  // Default thresholds (can be overridden per-bin via Redis)
  DEFAULT_WEIGHT_THRESHOLD: parseFloat(process.env.DEFAULT_WEIGHT_THRESHOLD) || 45,   // kg
  DEFAULT_VOLUME_THRESHOLD: parseFloat(process.env.DEFAULT_VOLUME_THRESHOLD) || 85,   // %
  DEFAULT_BATTERY_THRESHOLD: parseFloat(process.env.DEFAULT_BATTERY_THRESHOLD) || 20, // %
  DEFAULT_GAS_THRESHOLD: parseFloat(process.env.DEFAULT_GAS_THRESHOLD) || 300,       // ppm

  // Ambang tingkat CRITICAL (dokumentasi hardware §6 & §8).
  // Tingkat WARNING pakai threshold di atas (bisa dioverride per-tong).
  VOLUME_CRITICAL_THRESHOLD: parseFloat(process.env.VOLUME_CRITICAL_THRESHOLD) || 100, // % — tong penuh
  BATTERY_VOLTAGE_WARNING: parseFloat(process.env.BATTERY_VOLTAGE_WARNING) || 10.2,    // V — "Baterai Hampir Habis"
  BATTERY_VOLTAGE_CRITICAL: parseFloat(process.env.BATTERY_VOLTAGE_CRITICAL) || 9.6,   // V — alert darurat
  // Histeresis: alert baterai baru dianggap pulih di atas ambang ini (bukan tepat
  // di 10.2V), supaya tegangan yang naik-turun tidak bikin alert nyala-mati terus.
  BATTERY_VOLTAGE_RECOVERY: parseFloat(process.env.BATTERY_VOLTAGE_RECOVERY) || 10.5,  // V
};
