import winston from 'winston';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const logsDir = path.resolve(__dirname, '../../logs');

const { combine, timestamp, printf, colorize, errors } = winston.format;

const logFormat = printf(({ level, message, timestamp, stack }) => {
    return `${timestamp} [${level}]: ${stack || message}`;
});

export const logger = winston.createLogger({
    level: process.env.NODE_ENV === 'production' ? 'info' : 'debug',
    format: combine(
        errors({ stack: true }),
        timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
        logFormat
    ),
    transports: [
        // Console
        new winston.transports.Console({
            format: combine(
                colorize({ all: true }),
                errors({ stack: true }),
                timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
                logFormat
            ),
        }),
        // File — all logs
        new winston.transports.File({
            filename: path.join(logsDir, 'app.log'),
            maxsize: 5 * 1024 * 1024, // 5 MB
            maxFiles: 5,
            tailable: true,
        }),
        // File — errors only
        new winston.transports.File({
            filename: path.join(logsDir, 'error.log'),
            level: 'error',
            maxsize: 5 * 1024 * 1024,
            maxFiles: 5,
        }),
    ],
    exceptionHandlers: [
        new winston.transports.File({ filename: path.join(logsDir, 'exceptions.log') }),
    ],
    rejectionHandlers: [
        new winston.transports.File({ filename: path.join(logsDir, 'rejections.log') }),
    ],
});
