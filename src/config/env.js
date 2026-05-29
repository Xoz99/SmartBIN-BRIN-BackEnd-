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

  CORS_ORIGIN: process.env.CORS_ORIGIN || '',

  // Default thresholds (can be overridden per-bin via Redis)
  DEFAULT_WEIGHT_THRESHOLD: parseFloat(process.env.DEFAULT_WEIGHT_THRESHOLD) || 45,   // kg
  DEFAULT_VOLUME_THRESHOLD: parseFloat(process.env.DEFAULT_VOLUME_THRESHOLD) || 85,   // %
  DEFAULT_BATTERY_THRESHOLD: parseFloat(process.env.DEFAULT_BATTERY_THRESHOLD) || 20, // %
  DEFAULT_GAS_THRESHOLD: parseFloat(process.env.DEFAULT_GAS_THRESHOLD) || 300,       // ppm
};
