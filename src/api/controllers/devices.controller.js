import {
    sendCommand,
    getDeviceState,
    getAllDeviceStates,
} from '../../services/deviceControl.service.js';
import { success, error } from '../../utils/response.js';
import { logger } from '../../utils/logger.js';

/**
 * Remote control Raspberry Pi (main.py) lewat MQTT.
 * Semua endpoint butuh JWT + role ADMIN (lihat devices.route.js) karena
 * perintah di sini menggerakkan perangkat keras di lapangan.
 */

/** GET /devices/state — state terakhir semua node yang pernah lapor. */
export function listDeviceStates(_req, res) {
    return success(res, getAllDeviceStates(), 'Device states');
}

/**
 * GET /devices/:nodeId/status
 * Default baca state retained (instan, nol beban ke Pi).
 * ?live=1 → tanya langsung ke Pi (lebih akurat, ~1 detik, bisa timeout).
 */
export async function getStatus(req, res) {
    const { nodeId } = req.params;

    if (req.query.live === '1') {
        const ack = await sendCommand(nodeId, 'status');
        if (!ack.ok) return error(res, ack.error || 'Perangkat menolak perintah', 502);
        return success(res, ack.result, 'Status langsung dari perangkat');
    }

    const state = getDeviceState(nodeId);
    if (!state) {
        return error(
            res,
            `Belum pernah menerima status dari ${nodeId}. Pastikan main.py jalan dengan REMOTE_CONTROL=1.`,
            404
        );
    }
    return success(res, state, 'Status terakhir yang diketahui');
}

/**
 * POST /devices/:nodeId/command
 * Body: { action: string, args?: object }
 */
export async function postCommand(req, res) {
    const { nodeId } = req.params;
    const { action, args } = req.body || {};

    if (!action || typeof action !== 'string') {
        return error(res, "Field 'action' wajib diisi", 400);
    }

    const ack = await sendCommand(nodeId, action, args || {});

    logger.info(
        `[Devices] ${req.user?.email || req.user?.id} → ${nodeId}: ${action} ` +
        `(${ack.ok ? 'ok' : 'gagal'})`
    );

    if (!ack.ok) {
        return error(res, ack.error || 'Perangkat menolak perintah', 502, ack);
    }
    return success(res, ack.result, `Perintah '${action}' berhasil`);
}

/** POST /devices/:nodeId/camera/start */
export async function cameraStart(req, res) {
    const ack = await sendCommand(req.params.nodeId, 'camera_start');
    if (!ack.ok) return error(res, ack.error || 'Gagal menyalakan kamera', 502, ack);
    return success(res, ack.result, 'Kamera dinyalakan');
}

/** POST /devices/:nodeId/camera/stop */
export async function cameraStop(req, res) {
    const ack = await sendCommand(req.params.nodeId, 'camera_stop');
    if (!ack.ok) return error(res, ack.error || 'Gagal mematikan kamera', 502, ack);
    return success(res, ack.result, 'Kamera dimatikan');
}

/**
 * POST /devices/:nodeId/logs
 * Body: { on?: boolean, ttl?: number }  ttl dalam detik (default 300 di Pi).
 *
 * Log MASUK lewat WebSocket sebagai event DEVICE_LOG, bukan lewat response ini —
 * endpoint ini cuma menyalakan/mematikan keran. Streaming auto-mati setelah TTL
 * supaya tidak membakar kuota broker saat tidak ada yang menonton.
 */
export async function toggleLogs(req, res) {
    const { on = true, ttl } = req.body || {};
    const ack = await sendCommand(req.params.nodeId, 'log_stream', { on: !!on, ttl });
    if (!ack.ok) return error(res, ack.error || 'Gagal mengubah log stream', 502, ack);
    return success(
        res,
        ack.result,
        on ? 'Log streaming aktif — dengarkan event DEVICE_LOG di WebSocket' : 'Log streaming dimatikan'
    );
}
