import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
import { env } from '../config/env.js';
import { logger } from '../utils/logger.js';
import { error as errorResponse } from '../utils/response.js';

// Route imports
import authRoutes from './routes/auth.route.js';
import binsRoutes from './routes/bins.route.js';
import alertsRoutes from './routes/alerts.route.js';
import usersRoutes from './routes/users.route.js';
import areasRoutes from './routes/areas.route.js';
import pickupsRoutes from './routes/pickups.route.js';
import schedulesRoutes from './routes/schedules.route.js';
import prediksiRoutes from './routes/prediksi.route.js';
import depositsRoutes from './routes/deposits.route.js';
import disposalsRoutes from './routes/disposals.route.js';
import settingsRoutes from './routes/settings.route.js';
import analyticsRoutes from './routes/analytics.route.js';
import monitoringRoutes from './routes/monitoring.route.js';

export function createApp() {
    const app = express();

    // ─── Global Middleware ────────────────────────────────────────────────────
    app.use(helmet());

    app.use(cors({
        origin: env.NODE_ENV === 'production'
            ? (env.CORS_ORIGIN || 'http://localhost:3000')
            : '*',
        methods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
        allowedHeaders: ['Content-Type', 'Authorization'],
    }));

    app.use(express.json({ limit: '10mb' }));
    app.use(express.urlencoded({ extended: true }));

    app.use(morgan('combined', {
        stream: { write: (msg) => logger.http(msg.trim()) },
    }));

    // ─── Health check ─────────────────────────────────────────────────────────
    app.get('/health', (_req, res) => {
        res.status(200).json({ success: true, message: 'SmartBin Backend is running' });
    });

    // ─── Routes ───────────────────────────────────────────────────────────────
    app.use('/auth', authRoutes);
    app.use('/bins', binsRoutes);
    app.use('/alerts', alertsRoutes);
    app.use('/users', usersRoutes);
    app.use('/areas', areasRoutes);
    app.use('/pickups', pickupsRoutes);
    app.use('/schedules', schedulesRoutes);
    app.use('/prediksi', prediksiRoutes);
    app.use('/deposits', depositsRoutes);
    app.use('/disposals', disposalsRoutes);
    app.use('/settings', settingsRoutes);
    app.use('/analytics', analyticsRoutes);
    app.use('/monitoring', monitoringRoutes);

    // ─── 404 handler ─────────────────────────────────────────────────────────
    app.use((_req, res) => {
        errorResponse(res, 'Route not found', 404);
    });

    // ─── Global error handler ─────────────────────────────────────────────────
    // eslint-disable-next-line no-unused-vars
    app.use((err, _req, res, _next) => {
        logger.error('[Express Error]:', err);

        if (err.name === 'ZodError') {
            return errorResponse(res, 'Validation failed', 422, err.errors);
        }
        if (err.name === 'JsonWebTokenError' || err.name === 'TokenExpiredError') {
            return errorResponse(res, 'Unauthorized', 401);
        }

        errorResponse(res, err.message || 'Internal server error', err.statusCode || 500);
    });

    return app;
}
