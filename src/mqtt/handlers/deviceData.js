import { handleDeviceAck, setDeviceState } from '../../services/deviceControl.service.js';
import { broadcast } from '../../websocket/ws.js';
import { logger } from '../../utils/logger.js';

/**
 * Handler topik remote-control Raspi (smartbin/{nodeId}/device/*).
 * Sumber payload: backend/remote_control.py di Pi.
 */

/** device/state — snapshot retained: kamera, serial, sensor, online/offline. */
export async function handleDeviceState(nodeId, payload) {
    setDeviceState(nodeId, payload);
    logger.debug(`[Device] ${nodeId} state: online=${payload?.online} camera=${payload?.camera}`);
    await broadcast('DEVICE_STATE', { nodeId, ...payload });
}

/** device/ack — hasil satu perintah; melepas Promise yang menunggu di sendCommand(). */
export async function handleDeviceAckMsg(nodeId, payload) {
    handleDeviceAck(nodeId, payload);
    await broadcast('DEVICE_ACK', { nodeId, ...payload });
}

/** device/log — batch baris log saat streaming dinyalakan. Tidak disimpan ke DB. */
export async function handleDeviceLog(nodeId, payload) {
    if (payload?.dropped) {
        logger.warn(`[Device] ${nodeId}: ${payload.dropped} baris log terbuang (klien lambat)`);
    }
    await broadcast('DEVICE_LOG', { nodeId, ...payload });
}
