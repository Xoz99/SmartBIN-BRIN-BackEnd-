// Riset: bandingkan jalur komunikasi LoRa vs HTTP dari tabel sensor_logs.
// Ringkasan: jumlah paket, latency (avg/p50/p95), throughput, packet loss (dari seq),
// dan kualitas sinyal LoRa (rssi/snr). Opsional ekspor CSV mentah buat analisis lanjut.
//
// Pakai (di VPS, dari root repo):
//   node scripts/riset-transport.mjs                 # ringkasan semua node, semua waktu
//   node scripts/riset-transport.mjs --node bin-003  # cuma 1 node
//   node scripts/riset-transport.mjs --hours 24      # 24 jam terakhir
//   node scripts/riset-transport.mjs --from 2026-07-25 --to 2026-07-29  # rentang tanggal (UTC)
//   node scripts/riset-transport.mjs --csv riset.csv # + ekspor baris mentah ke CSV
//   node scripts/riset-transport.mjs --derive-http    # hitung throughput HTTP (tampil saja)
//   node scripts/riset-transport.mjs --write-derived  # tulis throughput HTTP ke DB (FE keisi)
//   node scripts/riset-transport.mjs --csv-summary ringkasan.csv  # tabel jadi (1 baris/transport)
import { PrismaClient } from '@prisma/client';
import { writeFileSync } from 'fs';

const prisma = new PrismaClient();

// ── parse argumen sederhana ──
const args = process.argv.slice(2);
const getFlag = (name) => {
    const i = args.indexOf(name);
    return i >= 0 ? (args[i + 1] ?? true) : undefined;
};
const nodeId = getFlag('--node');            // mis. bin-003
const hours  = getFlag('--hours');           // mis. 24
const csvOut = getFlag('--csv');             // mis. riset.csv (baris mentah)
const csvSummary = getFlag('--csv-summary'); // mis. ringkasan.csv (tabel jadi per-transport)
const fromArg = getFlag('--from');           // mis. 2026-07-25 (rentang tanggal, UTC)
const toArg   = getFlag('--to');             // mis. 2026-07-29

// Parse batas tanggal. Kalau cuma tanggal (tanpa jam): --from = awal hari, --to =
// AKHIR hari (23:59:59.999) → jadi "--to 2026-07-29" ikut menyertakan seluruh 29 Juli.
function parseBound(s, endOfDay) {
    if (!s || typeof s !== 'string') return null;
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) s += endOfDay ? 'T23:59:59.999Z' : 'T00:00:00.000Z';
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
}
const deriveHttp = args.includes('--derive-http'); // turunkan throughput HTTP dari latency×ukuran paket
const writeDerived = args.includes('--write-derived'); // tulis hasil hitung ke DB (biar FE keisi, bukan '-')

const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const pct = (xs, p) => {
    if (!xs.length) return null;
    const s = [...xs].sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
};
const f = (x, d = 1) => (x == null ? '—' : x.toFixed(d));

async function main() {
    // ── filter waktu & node ──
    const where = {};
    const gteDate = parseBound(fromArg, false);
    const lteDate = parseBound(toArg, true);
    if (gteDate || lteDate) {
        where.createdAt = {};
        if (gteDate) where.createdAt.gte = gteDate;
        if (lteDate) where.createdAt.lte = lteDate;
    } else if (hours) {
        where.createdAt = { gte: new Date(Date.now() - Number(hours) * 3600e3) };
    }
    if (nodeId) {
        const bin = await prisma.bin.findUnique({ where: { nodeId } });
        if (!bin) { console.log(`Node '${nodeId}' tidak ada di tabel bins.`); return; }
        where.binId = bin.id;
    }

    const logs = await prisma.sensorLog.findMany({
        where,
        select: {
            binId: true, transport: true, seq: true, latencyMs: true,
            throughputBps: true, packetLen: true, rssi: true, snr: true, createdAt: true,
        },
        orderBy: { createdAt: 'asc' },
    });

    if (!logs.length) { console.log('Belum ada data sensor_logs untuk filter ini.'); return; }

    // Ukuran paket acuan = median packetLen dari data yg PUNYA packetLen (real, mostly
    // LoRa). Dipakai utk MENURUNKAN throughput HTTP dari latency terukur, karena payload
    // HTTP identik dgn LoRa tapi device lama tak mengirim packetLen. Bukan angka karangan:
    // throughput = (ukuran_paket × 8) / latency_terukur.
    const allPkt = logs.map((r) => r.packetLen).filter((x) => x != null);
    const PKT_EST = pct(allPkt, 50);   // byte

    // Isi throughputBps HTTP yg kosong (in-memory) kalau --derive-http / --write-derived.
    let derivedCount = 0;
    if ((deriveHttp || writeDerived) && PKT_EST) {
        for (const r of logs) {
            if (r.transport === 'http' && r.throughputBps == null && r.latencyMs > 0) {
                const pkt = r.packetLen ?? PKT_EST;
                r.throughputBps = (pkt * 8) / (r.latencyMs / 1000);
                r._derived = true;
                derivedCount++;
            }
        }
    }

    // ── tulis hasil hitung ke DB (opsional) → biar FE nampilin angka, bukan '-' ──
    // Rumus SAMA dgn yg dipakai backend utk HTTP (packetLen×8 / latency). Cuma isi baris
    // yg throughputBps-nya masih NULL; baris yg udah keukur tidak disentuh. Hormati filter
    // --node / --hours biar konsisten dgn laporan.
    if (writeDerived && PKT_EST) {
        const cond = [`transport = 'http'`, `"throughputBps" IS NULL`, `"latencyMs" > 0`];
        if (where.binId) cond.push(`"binId" = '${where.binId}'`);
        if (hours) cond.push(`"createdAt" >= NOW() - INTERVAL '${Number(hours)} hours'`);
        const n = await prisma.$executeRawUnsafe(
            `UPDATE sensor_logs SET "throughputBps" = (${Number(PKT_EST)} * 8.0) / ("latencyMs" / 1000.0) `
            + `WHERE ${cond.join(' AND ')}`);
        console.log(`\n>> DB: ${n} baris HTTP diisi throughputBps (dihitung dari latency × ${PKT_EST} B). `
                    + `FE sekarang nampilin angka, bukan '-'.`);
    }

    // ── kelompokkan per transport ──
    const periodeLabel = (gteDate || lteDate)
        ? `, ${fromArg ?? '…'} → ${toArg ?? '…'}`
        : hours ? `, ${hours} jam terakhir` : ', semua waktu';
    const scope = `${nodeId ?? 'SEMUA node'}${periodeLabel}`;
    console.log(`\n=== RISET LoRa vs HTTP (${scope}) — total ${logs.length} paket ===\n`);

    const summaryRows = [];   // buat --csv-summary (tabel jadi per-transport)

    for (const t of ['lora', 'http']) {
        const rows = logs.filter((r) => r.transport === t);
        if (!rows.length) { console.log(`[${t.toUpperCase()}] belum ada data.\n`); continue; }

        const lat = rows.map((r) => r.latencyMs).filter((x) => x != null);
        const thr = rows.map((r) => r.throughputBps).filter((x) => x != null);
        const rssi = rows.map((r) => r.rssi).filter((x) => x != null);
        const snr = rows.map((r) => r.snr).filter((x) => x != null);

        // packet loss dari seq — SADAR RESTART. Tiap device restart, seq balik ke 0/1,
        // jadi seq itu gigi-gergaji, bukan monoton. Kita jalan per bin urut waktu (rows
        // sudah orderBy createdAt asc), pecah jadi segmen tiap seq TURUN (=restart), dan
        // cuma hitung paket hilang DI DALAM segmen yang sama. Lompatan mundur antar-segmen
        // tidak dihitung sbg loss (itu restart, bukan paket hilang).
        let gaps = 0, received = 0, resets = 0;
        const byBin = {};
        for (const r of rows) if (r.seq != null) (byBin[r.binId] ??= []).push(r.seq);
        for (const seqs of Object.values(byBin)) {
            let prev = null;
            for (const s of seqs) {
                if (prev != null) {
                    const d = s - prev;
                    if (d < 0) resets++;          // seq turun → device restart (segmen baru)
                    else if (d === 0) continue;   // duplikat → abaikan, jangan dihitung diterima
                    else if (d > 1) gaps += d - 1; // hilang (d-1) paket dalam segmen
                }
                received++;
                prev = s;
            }
        }
        const expected = received + gaps;
        const lossPct = expected ? (gaps / expected) * 100 : null;

        const nDerived = rows.filter((r) => r._derived).length;
        const thrLabel = nDerived ? 'throughput*' : 'throughput ';

        // Rentang tanggal data (createdAt paling awal & akhir) → biar tau periodenya.
        const times = rows.map((r) => r.createdAt.getTime());
        const fmt = (ms) => new Date(ms).toISOString().slice(0, 16).replace('T', ' ');

        console.log(`[${t.toUpperCase()}]  n=${rows.length}`);
        console.log(`  periode        : ${fmt(Math.min(...times))} → ${fmt(Math.max(...times))} (UTC)`);
        console.log(`  latency (ms)   : avg ${f(avg(lat))}  p50 ${f(pct(lat, 50))}  p95 ${f(pct(lat, 95))}`);
        console.log(`  ${thrLabel}    : avg ${f(avg(thr))} bps (${f(avg(thr) / 1000, 2)} kbps)`);
        console.log(`  packet loss    : ${f(lossPct, 1)}%  (hilang ${gaps}/${expected}, `
                    + `${resets} restart terdeteksi)`);
        if (t === 'lora') console.log(`  sinyal LoRa    : RSSI avg ${f(avg(rssi))} dBm  SNR avg ${f(avg(snr))} dB`);
        console.log('');

        summaryRows.push({
            transport: t,
            n: rows.length,
            periodeAwal: fmt(Math.min(...times)),
            periodeAkhir: fmt(Math.max(...times)),
            latencyAvgMs: avg(lat), latencyP50Ms: pct(lat, 50), latencyP95Ms: pct(lat, 95),
            throughputAvgKbps: avg(thr) != null ? avg(thr) / 1000 : null,
            throughputDihitung: nDerived > 0 ? 'ya' : 'tidak',
            packetLossPct: lossPct, paketHilang: gaps, paketDiterima: received,
            restartTerdeteksi: resets,
            rssiAvgDbm: avg(rssi), snrAvgDb: avg(snr),
        });
    }

    // Catatan kaki: muncul kalau ada throughput yg dihitung (netral, nggak alarmis).
    if ((deriveHttp || writeDerived) && PKT_EST) {
        console.log(`* throughput dihitung dari latency × ukuran payload (median ${PKT_EST} B)\n`);
    }

    // ── ekspor CSV mentah (opsional) ──
    if (csvOut && typeof csvOut === 'string') {
        // Kolom throughputDerived=1 menandai throughput HTTP yg diturunkan (bukan diukur),
        // biar pas analisis bisa dipisah dari yg asli — jaga transparansi riset.
        const head = 'createdAt,binId,transport,seq,latencyMs,throughputBps,throughputDerived,packetLen,rssi,snr';
        const body = logs.map((r) => [
            r.createdAt.toISOString(), r.binId, r.transport ?? '', r.seq ?? '',
            r.latencyMs ?? '', r.throughputBps ?? '', r._derived ? 1 : 0,
            r.packetLen ?? '', r.rssi ?? '', r.snr ?? '',
        ].join(',')).join('\n');
        writeFileSync(csvOut, `${head}\n${body}\n`);
        console.log(`CSV mentah → ${csvOut} (${logs.length} baris)\n`);
    }

    // ── ekspor CSV RINGKASAN (tabel jadi: 1 baris per transport) ──
    if (csvSummary && typeof csvSummary === 'string') {
        const cols = ['transport', 'n', 'periodeAwal', 'periodeAkhir',
            'latencyAvgMs', 'latencyP50Ms', 'latencyP95Ms',
            'throughputAvgKbps', 'throughputDihitung',
            'packetLossPct', 'paketHilang', 'paketDiterima', 'restartTerdeteksi',
            'rssiAvgDbm', 'snrAvgDb'];
        const round = (v) => (v == null ? '' : (Number.isInteger(v) ? v : Number(v.toFixed(2))));
        const head = cols.join(',');
        const body = summaryRows.map((s) => cols.map((c) => {
            const v = s[c];
            return typeof v === 'number' ? round(v) : (v ?? '');
        }).join(',')).join('\n');
        writeFileSync(csvSummary, `${head}\n${body}\n`);
        console.log(`CSV ringkasan → ${csvSummary} (${summaryRows.length} baris transport)\n`);
    }
}

main().catch((e) => { console.error('[ERROR]', e.message); process.exit(1); })
      .finally(() => prisma.$disconnect());
