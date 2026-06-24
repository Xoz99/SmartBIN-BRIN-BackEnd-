import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { sumWeightPerBinThisMonth } from '../models/deposit.model.js';
import { sumDisposalPerKecamatan } from '../models/disposal.model.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CSV_PATH = join(__dirname, '..', '..', 'data', 'sampahbandung_normal_monthly.csv');

// ─── Konstanta persis sama dengan Flask app.py ────────────────────────────────

const SEASONAL_FACTOR = {
  1:1.20, 2:1.05, 3:1.15, 4:1.10, 5:1.00, 6:0.95,
  7:0.88, 8:0.88, 9:0.92, 10:0.98, 11:1.05, 12:1.25,
};

const VOL_BASE = { metropolitan:15.0, 'semi urban':6.5, pedesaan:2.0 };
const CAPACITY_TON = { metropolitan:20.0, 'semi urban':10.0, pedesaan:4.0 };

const KEC_COORDS = {
  'ANDIR':            [-6.9178, 107.5867],
  'ANTAPANI':         [-6.9127, 107.6645],
  'ARCAMANIK':        [-6.9000, 107.6800],
  'ASTANAANYAR':      [-6.9400, 107.5967],
  'BABAKAN CIPARAY':  [-6.9450, 107.5800],
  'BANDUNG KIDUL':    [-6.9570, 107.6400],
  'BANDUNG KULON':    [-6.9350, 107.5700],
  'BANDUNG WETAN':    [-6.9070, 107.6230],
  'BATUNUNGGAL':      [-6.9250, 107.6320],
  'BOJONGLOA KIDUL':  [-6.9510, 107.5900],
  'BOJONGLOA KALER':  [-6.9380, 107.5830],
  'BUAHBATU':         [-6.9550, 107.6530],
  'CIBEUNYING KIDUL': [-6.9022, 107.6356],
  'CIBEUNYING KALER': [-6.8950, 107.6300],
  'CIBIRU':           [-6.9065, 107.7009],
  'CICENDO':          [-6.9050, 107.5900],
  'CIDADAP':          [-6.8745, 107.5970],
  'CINAMBO':          [-6.9280, 107.7050],
  'COBLONG':          [-6.8950, 107.6100],
  'GEDEBAGE':         [-6.9650, 107.7100],
  'KIARACONDONG':     [-6.9280, 107.6516],
  'LENGKONG':         [-6.9300, 107.6260],
  'MANDALAJATI':      [-6.8930, 107.6900],
  'PANYILEUKAN':      [-6.9560, 107.6950],
  'RANCASARI':        [-6.9550, 107.6780],
  'REGOL':            [-6.9400, 107.6080],
  'SUKAJADI':         [-6.8880, 107.5960],
  'SUKASARI':         [-6.8854, 107.5934],
  'SUMUR BANDUNG':    [-6.9164, 107.6133],
  'UJUNGBERUNG':      [-6.9000, 107.7056],
};

// ─── Cache CSV di memory (baca sekali saat startup) ───────────────────────────
let _cache = null;

function parseCSV() {
  if (_cache) return _cache;

  const text = readFileSync(CSV_PATH, 'utf-8');
  const lines = text.trim().split('\n');
  const header = lines[0].split(',');

  const idx = {
    kecamatan:  header.indexOf('kecamatan'),
    tps_id:     header.indexOf('tps_id'),
    area_type:  header.indexOf('area_type'),
    volume_ton: header.indexOf('volume_ton'),
    tahun:      header.indexOf('tahun'),
    bulan:      header.indexOf('bulan'),
  };

  _cache = lines.slice(1).map(line => {
    const col = line.split(',');
    return {
      kecamatan:  col[idx.kecamatan]?.trim(),
      tps_id:     col[idx.tps_id]?.trim(),
      area_type:  col[idx.area_type]?.trim(),
      volume_ton: parseFloat(col[idx.volume_ton]),
      tahun:      parseInt(col[idx.tahun]),
      bulan:      parseInt(col[idx.bulan]),
    };
  }).filter(r => r.kecamatan && !isNaN(r.volume_ton));

  return _cache;
}

function calcFillPct(areaType, year, month) {
  const growth   = 1 + (year - 2019) * 0.035;
  const seasonal = SEASONAL_FACTOR[month] ?? 1.0;
  const base     = VOL_BASE[areaType] ?? 2.0;
  const cap      = CAPACITY_TON[areaType] ?? 4.0;
  return Math.min(100, ((base * growth * seasonal * 1000) / (cap * 1000)) * 100);
}

// Master area_type 30 kecamatan Kota Bandung (sinkron dgn GeoJSON peta).
// Sumber: CSV (29 kecamatan) + "BANDUNG WETAN" (tidak ada di CSV → metropolitan).
// "CENANG" (bogus di CSV) sengaja dibuang.
const KEC_AREATYPE = {
  'ANDIR':'metropolitan', 'ANTAPANI':'semi urban', 'ARCAMANIK':'semi urban',
  'ASTANAANYAR':'semi urban', 'BABAKAN CIPARAY':'semi urban', 'BANDUNG KIDUL':'pedesaan',
  'BANDUNG KULON':'metropolitan', 'BANDUNG WETAN':'metropolitan', 'BATUNUNGGAL':'semi urban',
  'BOJONGLOA KALER':'semi urban', 'BOJONGLOA KIDUL':'semi urban', 'BUAHBATU':'metropolitan',
  'CIBEUNYING KALER':'metropolitan', 'CIBEUNYING KIDUL':'metropolitan', 'CIBIRU':'semi urban',
  'CICENDO':'metropolitan', 'CIDADAP':'pedesaan', 'CINAMBO':'pedesaan', 'COBLONG':'metropolitan',
  'GEDEBAGE':'pedesaan', 'KIARACONDONG':'semi urban', 'LENGKONG':'metropolitan',
  'MANDALAJATI':'pedesaan', 'PANYILEUKAN':'pedesaan', 'RANCASARI':'pedesaan',
  'REGOL':'metropolitan', 'SUKAJADI':'semi urban', 'SUKASARI':'semi urban',
  'SUMUR BANDUNG':'metropolitan', 'UJUNGBERUNG':'semi urban',
};

// Cari kecamatan terdekat dari koordinat bin (Kota Bandung + Bandung Raya)
function nearestKecamatan(lat, lng) {
  let best = null, bestD = Infinity;
  const check = (kec, klat, klng) => {
    const d = (lat - klat) ** 2 + (lng - klng) ** 2;
    if (d < bestD) { bestD = d; best = kec; }
  };
  for (const [kec, [klat, klng]] of Object.entries(KEC_COORDS)) check(kec, klat, klng);
  return best;
}

// ─── Pemetaan bin -> kecamatan via POLIGON (point-in-polygon) ──────────────────
// Supaya konsisten dengan warna zona di peta: bin yang berada DI DALAM poligon
// suatu kecamatan dihitung ke kecamatan itu (bukan sekadar centroid terdekat).
const GEOJSON_URL = 'https://cdn.jsdelivr.net/gh/tryfatur/geojson-bandung@master/3273-kota-bandung-level-kecamatan.json';
let _geoCache = null; // { features: [{ name, rings }] }

async function loadKecPolygons() {
  if (_geoCache) return _geoCache;
  try {
    const res = await fetch(GEOJSON_URL, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error('geojson http ' + res.status);
    const gj = await res.json();
    const features = [];
    for (const f of gj.features ?? []) {
      const name = (f.properties?.nama_kecamatan ?? '').toUpperCase().trim();
      if (!name || !f.geometry) continue;
      // Kumpulkan semua ring [lng,lat] dari Polygon / MultiPolygon
      const rings = [];
      if (f.geometry.type === 'Polygon') {
        for (const ring of f.geometry.coordinates) rings.push(ring);
      } else if (f.geometry.type === 'MultiPolygon') {
        for (const poly of f.geometry.coordinates) for (const ring of poly) rings.push(ring);
      }
      if (rings.length) features.push({ name, rings });
    }
    _geoCache = { features };
  } catch {
    _geoCache = { features: [] }; // gagal -> nanti fallback ke nearestKecamatan
  }
  return _geoCache;
}

// Ray-casting point-in-polygon. ring = array [lng, lat].
function pointInRing(lng, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    const intersect = ((yi > lat) !== (yj > lat)) &&
      (lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

// Kecamatan yang poligonnya mengandung titik (lat,lng); null kalau tidak ada.
function kecByPolygon(geo, lat, lng) {
  for (const f of geo.features) {
    for (const ring of f.rings) {
      if (pointInRing(lng, lat, ring)) return f.name;
    }
  }
  return null;
}

/**
 * Total setoran AKTUAL (ton) per kecamatan bulan ini, dari tabel deposits.
 * Dipakai untuk membuat fill_pct & forecast LSTM/rule ikut data nyata (lebih realtime).
 * @returns {Promise<Map<string, number>>} key = NAMA KECAMATAN (UPPERCASE), value = ton
 */
async function depositTonPerKecamatanThisMonth() {
  const tonPerKec = new Map();
  try {
    const perBin = await sumWeightPerBinThisMonth();
    const geo = await loadKecPolygons();
    for (const b of perBin) {
      // Utamakan poligon (konsisten dgn warna zona di peta), fallback ke centroid terdekat.
      const kec = kecByPolygon(geo, b.lat, b.lng) ?? nearestKecamatan(b.lat, b.lng);
      if (!kec) continue;
      const ton = (b.kg ?? 0) / 1000; // kg -> ton (satuan model)
      tonPerKec.set(kec, (tonPerKec.get(kec) ?? 0) + ton);
    }
    // Kurangi sampah yang sudah DIMUSNAHKAN (langsung, tanpa masa tunggu).
    const disposal = await sumDisposalPerKecamatan();
    for (const [kec, d] of disposal) {
      if (!tonPerKec.has(kec)) continue;
      const sisaTon = Math.max(0, tonPerKec.get(kec) - (d.musnahKg ?? 0) / 1000);
      tonPerKec.set(kec, sisaTon);
    }
  } catch { /* DB error -> map kosong, prediksi pakai baseline saja */ }
  return tonPerKec;
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Semua kecamatan dengan fill_pct dan alert.
 * Baseline dari CSV historis + DILIPAT data setoran AKTUAL warga (tabel deposits).
 * Jadi fill & overload tiap daerah variatif sesuai aktivitas nyata.
 */
export async function getAllZona() {
  const rows = parseCSV();
  const now   = new Date();
  const year  = now.getFullYear();
  const month = now.getMonth() + 1;

  // Jumlah TPS unik per kecamatan dari CSV (untuk kapasitas), fallback 3
  const tpsSet = new Map();
  for (const row of rows) {
    const kec = row.kecamatan.toUpperCase();
    if (!tpsSet.has(kec)) tpsSet.set(kec, new Set());
    tpsSet.get(kec).add(row.tps_id);
  }

  // Data AKTUAL: total kg setoran bulan ini → dipetakan ke kecamatan via POLIGON
  // (bin di dalam poligon kecamatan X dihitung ke X), fallback centroid terdekat.
  const kgPerKec = new Map();      // total setoran (sebelum dikurangi pemusnahan)
  let disposal = new Map();        // {kec: {musnahKg}}
  try {
    const perBin = await sumWeightPerBinThisMonth();
    const geo = await loadKecPolygons();
    for (const b of perBin) {
      const kec = kecByPolygon(geo, b.lat, b.lng) ?? nearestKecamatan(b.lat, b.lng);
      if (kec) kgPerKec.set(kec, (kgPerKec.get(kec) ?? 0) + b.kg);
    }
    disposal = await sumDisposalPerKecamatan();
  } catch { /* DB error → pakai baseline saja */ }

  // Emit SEMUA 30 kecamatan Kota Bandung (master) — selalu sinkron dgn GeoJSON peta
  const zona = [];
  for (const [kec, area_type] of Object.entries(KEC_AREATYPE)) {
    const coords   = KEC_COORDS[kec];
    if (!coords) continue;
    const count    = tpsSet.get(kec)?.size ?? 3;
    const baseline = calcFillPct(area_type, year, month);
    const capKg    = count * (CAPACITY_TON[area_type] ?? 4.0) * 1000;

    const terkumpulKg = kgPerKec.get(kec) ?? 0;             // total setoran
    const d           = disposal.get(kec) ?? { musnahKg: 0 };
    // actual = setoran dikurangi yang sudah DIMUSNAHKAN (langsung)
    const actualKg = Math.max(0, terkumpulKg - (d.musnahKg ?? 0));

    const extraPct = capKg > 0 ? (actualKg / capKg) * 100 : 0;
    const fill_pct = Math.min(100, Math.round(baseline + extraPct));
    const alert    = fill_pct >= 85 ? 'KRITIS' : fill_pct >= 65 ? 'WASPADA' : 'AMAN';
    zona.push({
      id: kec.toLowerCase().replace(/ /g, '_'),
      kecamatan: kec, area_type,
      lat: coords[0], lon: coords[1],
      fill_pct, alert, total_tps: count,
      actual_kg: Math.round(actualKg),
      // rincian untuk panel Manajemen TPA
      terkumpul_kg:     Math.round(terkumpulKg),
      proses_musnah_kg: 0,                          // tak ada masa tunggu lagi
      musnah_resmi_kg:  Math.round(d.musnahKg ?? 0), // total sudah dimusnahkan
      sisa_tpa_kg:      Math.round(actualKg),
    });
  }

  return zona;
}

/**
 * Ringkasan Bandung — ekuivalen Flask GET /api/summary
 */
export async function getSummary() {
  const zona = await getAllZona();
  const counts = { AMAN: 0, WASPADA: 0, KRITIS: 0 };
  let totalFill = 0;
  zona.forEach(z => { counts[z.alert]++; totalFill += z.fill_pct; });
  return {
    total_kecamatan: zona.length,
    rata_fill_pct:   Math.round(totalFill / zona.length),
    counts,
    updated_at: new Date().toISOString(),
  };
}

/**
 * Prediksi rule-based per kecamatan — ekuivalen Flask GET /api/prediksi/:tps_id
 * (fallback tanpa LSTM karena model Python tidak jalan di Node)
 */
export async function getPrediksiKecamatan(kecamatan) {
  const rows = parseCSV();
  const kecUpper = kecamatan.toUpperCase();

  // Ambil semua baris kecamatan ini, sort by tanggal
  const kecRows = rows
    .filter(r => r.kecamatan.toUpperCase() === kecUpper)
    .sort((a, b) => a.tahun !== b.tahun ? a.tahun - b.tahun : a.bulan - b.bulan);

  if (kecRows.length === 0) return null;

  const area_type = kecRows[0].area_type;
  const LBL = ['Jan','Feb','Mar','Apr','Mei','Jun','Jul','Agu','Sep','Okt','Nov','Des'];
  const now = new Date();
  const baseYear = now.getFullYear();
  const baseMonth = now.getMonth() + 1;

  // Agregat volume rata-rata per bulan dari CSV
  const monthAvg = new Map();
  const monthCount = new Map();
  for (const r of kecRows) {
    const key = `${r.tahun}-${r.bulan}`;
    monthAvg.set(key, (monthAvg.get(key) ?? 0) + r.volume_ton);
    monthCount.set(key, (monthCount.get(key) ?? 0) + 1);
  }
  for (const [k] of monthAvg) {
    monthAvg.set(k, monthAvg.get(k) / monthCount.get(k));
  }

  const getVol = (y, m) => {
    const key = `${y}-${m}`;
    if (monthAvg.has(key)) return monthAvg.get(key);
    // rule-based fallback
    const growth = 1 + (y - 2019) * 0.035;
    return (VOL_BASE[area_type] ?? 2.0) * growth * (SEASONAL_FACTOR[m] ?? 1.0);
  };

  // Data AKTUAL: setoran warga kecamatan ini bulan ini (ton) → bikin prediksi realtime
  const tonPerKec  = await depositTonPerKecamatanThisMonth();
  const actualTon  = tonPerKec.get(kecUpper) ?? 0;

  // Volume bulan ini = baseline CSV + setoran nyata bulan ini
  const baseCurrent = getVol(baseYear, baseMonth);
  const volCurrent  = baseCurrent + actualTon;

  // Faktor "momentum" dari aktivitas nyata: kalau setoran bulan ini di atas baseline,
  // forecast ikut terangkat proporsional (capped supaya tidak meledak).
  const momentum = baseCurrent > 0
    ? Math.min(0.5, actualTon / baseCurrent)   // maks +50%
    : (actualTon > 0 ? 0.5 : 0);

  const timeline = [];

  // 3 bulan lalu (murni historis CSV)
  for (let i = 3; i >= 1; i--) {
    let m = baseMonth - i, y = baseYear;
    if (m <= 0) { m += 12; y--; }
    timeline.push({ bulan:m, tahun:y, volume_ton:Math.round(getVol(y,m)*100)/100,
      label:`${LBL[m-1]} ${y}`, type:'history' });
  }

  // Bulan ini (baseline + setoran aktual)
  timeline.push({ bulan:baseMonth, tahun:baseYear,
    volume_ton:Math.round(volCurrent*100)/100,
    label:`${LBL[baseMonth-1]} ${baseYear}`, type:'current' });

  // 3 bulan forecast: seasonal CSV diangkat oleh momentum setoran nyata
  const predictions = [];
  for (let i = 1; i <= 3; i++) {
    let m = baseMonth + i, y = baseYear;
    if (m > 12) { m -= 12; y++; }
    // momentum meluruh tiap bulan (pengaruh setoran berkurang ke depan)
    const decay = momentum * Math.pow(0.6, i - 1);
    const vol = Math.round(getVol(y,m) * (1 + decay) * 100) / 100;
    const pt = { bulan:m, tahun:y, volume_ton:vol, label:`${LBL[m-1]} ${y}`, type:'forecast' };
    timeline.push(pt);
    predictions.push(pt);
  }

  return {
    kecamatan: kecUpper,
    area_type,
    model_used: 'rule-based-seasonal + setoran-aktual',
    actual_ton: Math.round(actualTon * 1000) / 1000,
    timeline,
    predictions,
  };
}
