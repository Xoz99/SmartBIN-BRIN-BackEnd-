import { PrismaClient } from '@prisma/client';
import { logger } from '../utils/logger.js';

let prisma;

if (!global.__prisma) {
    global.__prisma = new PrismaClient({
        log: [
            { emit: 'event', level: 'query' },
            { emit: 'event', level: 'error' },
            { emit: 'event', level: 'warn' },
        ],
    });

    global.__prisma.$on('error', (e) => {
        logger.error('[Prisma] DB Error:', e);
    });

    global.__prisma.$on('warn', (e) => {
        logger.warn('[Prisma] DB Warning:', e);
    });
}

prisma = global.__prisma;

export { prisma };

/**
 * Verify DB connection — call on startup
 */
export async function connectDB() {
    try {
        await prisma.$connect();
        await prisma.$queryRaw`SELECT 1`;
        logger.info('[DB] PostgreSQL connected successfully');
    } catch (err) {
        logger.error('[DB] Failed to connect to PostgreSQL:', err.message);
        throw err;
    }
}
