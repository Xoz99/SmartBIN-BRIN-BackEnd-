import { handleSensorData } from '../../mqtt/handlers/sensorData.js';
import { findBinByNodeId } from '../../models/bin.model.js';
import { success, error } from '../../utils/response.js';

// POST /ingest/sensor
// Jalur HTTP untuk Raspi LoRa-RX yang push data sensor ke server pusat —
// alternatif MQTT (mis. gateway hanya punya jalur internet, bukan broker MQTT).
// Memakai ulang handleSensorData supaya perilakunya IDENTIK dengan jalur MQTT
// (validasi, simpan SensorLog, cache Redis, alert, broadcast WebSocket).
//
// Body (dua bentuk diterima):
//   { "nodeId": "bin-003", "weight": 45.2, "volume": 87, "battery": 78, "gas": 150, "rssi": -65 }
//   { "nodeId": "bin-003", "payload": { ...sama seperti di atas... } }
// Auth: header X-Device-Key = DEVICE_INGEST_KEY (deviceAuth).
export async function sensorIngestController(req, res) {
    try {
        const body = req.body || {};
        const nodeId = body.nodeId || body.node;
        if (!nodeId) return error(res, 'nodeId is required', 400);

        // Node harus terdaftar (handleSensorData akan discard diam-diam kalau
        // tidak ada) → di sini kita balas 404 supaya perangkat tahu salah config.
        const bin = await findBinByNodeId(nodeId);
        if (!bin) return error(res, `Unknown nodeId: ${nodeId}`, 404);

        // payload = body.payload kalau ada, kalau tidak seluruh body minus identitas.
        let payload = body.payload;
        if (!payload || typeof payload !== 'object') {
            const { nodeId: _n, node: _nd, ...rest } = body;
            payload = rest;
        }

        await handleSensorData(nodeId, payload);
        return success(res, { nodeId }, 'Sensor data accepted', 202);
    } catch (err) {
        return error(res, err.message, 500);
    }
}
