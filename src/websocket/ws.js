import { WebSocketServer } from 'ws';
import { verifyToken } from '../services/auth.service.js';
import { logger } from '../utils/logger.js';

let wss;

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

    logger.info('[WebSocket] Server initialized');
}

/**
 * Broadcast a typed event to ALL connected WebSocket clients
 *
 * @param {'BIN_UPDATE'|'ALERT_NEW'|'ALERT_RESOLVED'|'BIN_STATUS'|'CLASSIFICATION_NEW'|'PICKUP_COMPLETED'|'PICKUP_CONFIRMED'} event
 * @param {object} payload
 */
export function broadcast(event, payload) {
    if (!wss) return;

    const message = JSON.stringify({ event, payload });
    let sent = 0;

    wss.clients.forEach((ws) => {
        if (ws.readyState === ws.OPEN) {
            ws.send(message);
            sent++;
        }
    });

    logger.debug(`[WebSocket] Broadcast '${event}' to ${sent} client(s)`);
}

export { wss };
