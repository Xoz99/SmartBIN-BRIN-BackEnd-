/**
 * Simpan frame kamera TERAKHIR per bin di memori (ephemeral).
 * Bukan riwayat — cuma buat monitoring live-ish di dashboard. Pi push frame
 * tiap ~1.5s; frame dianggap BASI (kamera mati) kalau > STALE_MS tak ada update.
 */
const _frames = new Map(); // nodeId -> { buf: Buffer, ts: number }
const STALE_MS = 15000;

export function setFrame(nodeId, buf) {
    _frames.set(nodeId, { buf, ts: Date.now() });
}

export function getFrame(nodeId) {
    const f = _frames.get(nodeId);
    if (!f) return null;
    if (Date.now() - f.ts > STALE_MS) return null; // basi → anggap kamera offline
    return f;
}
