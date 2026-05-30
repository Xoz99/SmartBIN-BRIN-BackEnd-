import { WebSocketServer } from 'ws';
import { verifyToken } from '../services/auth.service.js';
import { redisClient, createRedisClient } from '../config/redis.js';
import { logger } from '../utils/logger.js';

let wss;
let subClient;

// Redis channel used to bridge broadcasts across processes (API, MQTT handlers, CLI scripts)
const WS_CHANNEL = 'ws:broadcast';

/**
 * Initialize WebSocket server attached to the existing HTTP server
 * @param {import('http').Server} httpServer
 */
export function initWebSocket(httpServer) {
    wss = new WebSocketServer({
        server: httpServer,
        verifyClient: (info, done) => {
            try {
                const url = new URL(info.req.url, `http://${info.req.headers.host}`);
                const token = url.searchParams.get('token');
                if (!token) {
                    done(false, 401, 'Authentication required');
                    return;
                }
                const decoded = verifyToken(token);
                info.req.user = decoded;
                done(true);
            } catch {
                done(false, 401, 'Invalid or expired token');
            }
        },
    });

    wss.on('connection', (ws, req) => {
        const clientIp = req.socket.remoteAddress;
        ws.user = req.user;
        logger.info(`[WebSocket] Client connected: ${clientIp} (user: ${req.user?.email || 'unknown'})`);

        // Send welcome
        ws.send(JSON.stringify({ event: 'CONNECTED', payload: { message: 'SmartBin WebSocket ready' } }));

        // Ping/pong heartbeat
        ws.isAlive = true;
        ws.on('pong', () => { ws.isAlive = true; });

        ws.on('message', (data) => {
            // For now, client messages are just logged (e.g. subscribe to specific bin)
            try {
                const msg = JSON.parse(data.toString());
                logger.debug('[WebSocket] Client message:', msg);
            } catch {
                logger.debug('[WebSocket] Raw message:', data.toString());
            }
        });

        ws.on('close', () => {
            logger.info(`[WebSocket] Client disconnected: ${clientIp}`);
        });

        ws.on('error', (err) => {
            logger.warn(`[WebSocket] Client error (${clientIp}):`, err.message);
        });
    });

    // Heartbeat interval — ping every 30s, drop dead connections
    const heartbeat = setInterval(() => {
        wss.clients.forEach((ws) => {
            if (!ws.isAlive) {
                logger.debug('[WebSocket] Terminating dead connection');
                return ws.terminate();
            }
            ws.isAlive = false;
            ws.ping();
        });
    }, 30_000);

    wss.on('close', () => clearInterval(heartbeat));

    // Subscribe to the Redis bridge so broadcasts published by *any* process reach our clients
    setupRedisSubscriber();

    logger.info('[WebSocket] Server initialized');
}

/** Server-process Redis subscriber: receives broadcasts and fans them out to local WS clients. */
function setupRedisSubscriber() {
    try {
        subClient = createRedisClient().duplicate();
        subClient.on('error', (err) => logger.warn(`[WebSocket] Redis subscriber error: ${err.message}`));
        subClient.subscribe(WS_CHANNEL, (err) => {
            if (err) logger.error(`[WebSocket] Failed to subscribe '${WS_CHANNEL}': ${err.message}`);
            else logger.info(`[WebSocket] Subscribed to Redis channel '${WS_CHANNEL}'`);
        });
        subClient.on('message', (channel, message) => {
            if (channel === WS_CHANNEL) sendToLocalClients(message);
        });
    } catch (err) {
        logger.warn(`[WebSocket] Redis subscriber unavailable: ${err.message}. Cross-process broadcast disabled.`);
    }
}

/** Send a pre-serialized message to all OPEN clients connected to THIS process. */
function sendToLocalClients(message) {
    if (!wss) return;
    let sent = 0;
    wss.clients.forEach((ws) => {
        if (ws.readyState === ws.OPEN) {
            ws.send(message);
            sent++;
        }
    });
    logger.debug(`[WebSocket] Delivered to ${sent} client(s)`);
}

/**
 * Broadcast a typed event to ALL connected WebSocket clients.
 *
 * Publishes to Redis so it works regardless of which process calls it
 * (HTTP API, MQTT handlers, or standalone CLI scripts). The server process
 * receives it via {@link setupRedisSubscriber} and fans it out to its clients.
 * Falls back to a direct in-process send when Redis is unavailable.
 *
 * @param {'BIN_UPDATE'|'ALERT_NEW'|'ALERT_RESOLVED'|'BIN_STATUS'|'CLASSIFICATION_NEW'|'PICKUP_COMPLETED'|'PICKUP_CONFIRMED'} event
 * @param {object} payload
 */
export async function broadcast(event, payload) {
    const message = JSON.stringify({ event, payload });

    if (redisClient && redisClient.status === 'ready') {
        try {
            await redisClient.publish(WS_CHANNEL, message);
            return;
        } catch (err) {
            logger.warn(`[WebSocket] Redis publish failed (${err.message}); falling back to direct send`);
        }
    }

    // Fallback: Redis down — deliver directly to clients of this process (server only)
    sendToLocalClients(message);
}

export { wss };
