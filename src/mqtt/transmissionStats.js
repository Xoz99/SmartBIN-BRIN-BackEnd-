/**
 * ====================================================
 * Transmission Stats — byterate/throughput per node
 * ====================================================
 * Mengukur laju transmisi data MQTT yang diterima core server dari tiap node.
 * Diukur dari ukuran byte payload asli (message.length) di titik terima MQTT —
 * satu-satunya tempat byterate benar-benar valid.
 *
 * Algoritma sliding-window di-port dari cobaRaspi/network/wifi_statistics.py
 * supaya angka konsisten dengan CLI monitoring.
 * ====================================================
 */

const WINDOW_SIZE = 10.0; // detik — sliding window untuk rate & throughput
const LATENCY_SKEW_LIMIT = 10_000; // ms — abaikan latency > ini (clock skew ekstrem)

class NodeStats {
    constructor(nodeId) {
        this.nodeId = nodeId;

        // window: array of [timestampSec, bytes]
        this.packetWindow = [];
        this.latencyWindow = []; // array of [timestampSec, latencyMs]

        this.previousSequence = null;

        // kumulatif
        this.packets = 0;
        this.received = 0;
        this.loss = 0;
        this.lossPercent = 0;

        // real-time (dipertahankan nilai terakhir saat window kosong)
        this.lastPacketRate = 0;
        this.lastThroughput = 0; // KB/s
        this.lastLatency = 0; // ms

        this.lastSequence = 0;
        this.lastPacketTime = 0; // detik
    }

    /**
     * Catat satu pesan yang diterima.
     * @param {number} byteLength ukuran payload dalam byte (message.length)
     * @param {{ seq?: number, timestamp?: number }} meta
     */
    record(byteLength, { seq = null, timestamp = null } = {}) {
        const now = Date.now() / 1000;

        this.lastPacketTime = now;
        this.packets += 1;
        this.received += 1;
        this.packetWindow.push([now, byteLength]);

        // ── Sequence & packet loss ────────────────────────────────────────────
        if (seq != null && Number.isFinite(seq)) {
            if (this.previousSequence != null) {
                const expected = this.previousSequence + 1;
                if (seq > expected) this.loss += seq - expected;
            }
            this.previousSequence = seq;
            this.lastSequence = seq;
        }
        if (this.packets > 0) {
            this.lossPercent = (this.loss / this.packets) * 100;
        }

        // ── Latency ───────────────────────────────────────────────────────────
        // timestamp diasumsikan detik epoch dari pengirim. Pakai abs() untuk clock skew.
        if (timestamp != null && Number.isFinite(timestamp)) {
            const latency = Math.abs((now - timestamp) * 1000);
            if (latency < LATENCY_SKEW_LIMIT) {
                this.latencyWindow.push([now, latency]);
            }
        }

        this._recompute(now);
    }

    /** Refresh nilai turunan tanpa pesan baru (mis. untuk lastPacketAge). */
    refresh(now = Date.now() / 1000) {
        this._recompute(now);
    }

    _recompute(now) {
        // Buang data di luar window
        while (this.packetWindow.length && now - this.packetWindow[0][0] > WINDOW_SIZE) {
            this.packetWindow.shift();
        }
        while (this.latencyWindow.length && now - this.latencyWindow[0][0] > WINDOW_SIZE) {
            this.latencyWindow.shift();
        }

        // Packet rate & throughput (pertahankan nilai terakhir kalau window kosong)
        if (this.packetWindow.length) {
            const duration = Math.max(now - this.packetWindow[0][0], 1.0);
            const totalBytes = this.packetWindow.reduce((sum, p) => sum + p[1], 0);
            this.lastPacketRate = this.packetWindow.length / duration;
            this.lastThroughput = totalBytes / duration / 1024; // KB/s
        }

        // Latency rata-rata window
        if (this.latencyWindow.length) {
            const sum = this.latencyWindow.reduce((s, l) => s + l[1], 0);
            this.lastLatency = sum / this.latencyWindow.length;
        }
    }

    toJSON(now = Date.now() / 1000) {
        this.refresh(now);
        return {
            nodeId: this.nodeId,
            packets: this.packets,
            received: this.received,
            loss: this.loss,
            lossPercent: Number(this.lossPercent.toFixed(2)),
            packetRate: Number(this.lastPacketRate.toFixed(2)),
            throughputKBps: Number(this.lastThroughput.toFixed(2)),
            latencyMs: Number(this.lastLatency.toFixed(2)),
            lastSeq: this.lastSequence,
            lastPacketAgeSec:
                this.lastPacketTime > 0
                    ? Number((now - this.lastPacketTime).toFixed(1))
                    : null,
        };
    }
}

class TransmissionStats {
    constructor() {
        /** @type {Map<string, NodeStats>} */
        this.nodes = new Map();
    }

    /**
     * @param {string} nodeId
     * @param {number} byteLength
     * @param {{ seq?: number, timestamp?: number }} [meta]
     */
    record(nodeId, byteLength, meta = {}) {
        if (!nodeId) return;
        let node = this.nodes.get(nodeId);
        if (!node) {
            node = new NodeStats(nodeId);
            this.nodes.set(nodeId, node);
        }
        node.record(byteLength, meta);
    }

    getByNode(nodeId) {
        const node = this.nodes.get(nodeId);
        return node ? node.toJSON() : null;
    }

    getAll() {
        const now = Date.now() / 1000;
        const perNode = [...this.nodes.values()].map((n) => n.toJSON(now));

        const aggregate = perNode.reduce(
            (acc, n) => {
                acc.packets += n.packets;
                acc.received += n.received;
                acc.loss += n.loss;
                acc.throughputKBps += n.throughputKBps;
                acc.packetRate += n.packetRate;
                return acc;
            },
            { packets: 0, received: 0, loss: 0, throughputKBps: 0, packetRate: 0 },
        );
        aggregate.throughputKBps = Number(aggregate.throughputKBps.toFixed(2));
        aggregate.packetRate = Number(aggregate.packetRate.toFixed(2));
        aggregate.lossPercent =
            aggregate.packets > 0
                ? Number(((aggregate.loss / aggregate.packets) * 100).toFixed(2))
                : 0;

        return { nodes: perNode, aggregate };
    }
}

export const transmissionStats = new TransmissionStats();
