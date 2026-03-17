/* PowerWizard Dashboard — app.js */
"use strict";

const refreshMs = 5000;

// ── Gauge drawing ─────────────────────────────────────────────────────────────
// Renders a semicircle gauge (180° sweep).
// Colors: left=green, center=yellow, right=red (butt linecap to avoid red cap dot)

function arcPath(cx, cy, r, fromFrac, toFrac, sweep) {
  // fromFrac/toFrac: 0..1 along the 180° arc (left to right)
  // sweep = total sweep angle in degrees (default 180)
  const totalDeg = sweep || 180;
  const startDeg = 180 + (fromFrac * totalDeg);
  const endDeg   = 180 + (toFrac   * totalDeg);
  const startRad = (startDeg * Math.PI) / 180;
  const endRad   = (endDeg   * Math.PI) / 180;
  const x1 = cx + r * Math.cos(startRad);
  const y1 = cy + r * Math.sin(startRad);
  const x2 = cx + r * Math.cos(endRad);
  const y2 = cy + r * Math.sin(endRad);
  const largeArc = (toFrac - fromFrac) > 0.5 ? 1 : 0;
  return `M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`;
}

function makeGaugeSVG(value, min, max, label, unit, size) {
  size = size || 140;
  const cx = size / 2;
  const cy = size / 2 + 10;
  const r = size * 0.38;
  const sw = size * 0.07; // stroke width

  const frac = Math.max(0, Math.min(1, (value - min) / (max - min)));

  // Needle angle: 180° → 360° (sweep=180)
  const needleDeg = 180 + frac * 180;
  const needleRad = (needleDeg * Math.PI) / 180;
  const nx = cx + (r - sw / 2 - 2) * Math.cos(needleRad);
  const ny = cy + (r - sw / 2 - 2) * Math.sin(needleRad);
  // Start needle slightly inside center
  const ox = cx + 6 * Math.cos(needleRad);
  const oy = cy + 6 * Math.sin(needleRad);

  const dispVal = isNaN(value) ? '—' : value.toFixed(value < 10 ? 2 : 1);

  return `<svg width="${size}" height="${Math.round(size * 0.72)}" viewBox="0 0 ${size} ${cy + 18}" xmlns="http://www.w3.org/2000/svg">
  <!-- background arc -->
  <path d="${arcPath(cx, cy, r, 0, 1)}" fill="none" stroke="#2e3350" stroke-width="${sw}" stroke-linecap="round"/>
  <!-- green segment -->
  <path d="${arcPath(cx, cy, r, 0, 0.34)}" fill="none" stroke="#22c55e" stroke-width="${sw}" stroke-linecap="butt"/>
  <!-- yellow segment -->
  <path d="${arcPath(cx, cy, r, 0.34, 0.67)}" fill="none" stroke="#f59e0b" stroke-width="${sw}" stroke-linecap="butt"/>
  <!-- red segment -->
  <path d="${arcPath(cx, cy, r, 0.67, 1)}" fill="none" stroke="#ef4444" stroke-width="${sw}" stroke-linecap="butt"/>
  <!-- needle -->
  <line x1="${ox}" y1="${oy}" x2="${nx}" y2="${ny}"
        stroke="#e2e8f0" stroke-width="${Math.max(1.5, sw * 0.35)}" stroke-linecap="round"/>
  <!-- value -->
  <text x="${cx}" y="${cy + 14}" text-anchor="middle" fill="#e2e8f0"
        font-size="${size * 0.13}" font-weight="700" font-family="system-ui,sans-serif">
    ${dispVal} <tspan font-size="${size * 0.09}" fill="#8892a4">${unit}</tspan>
  </text>
  <!-- label -->
  <text x="${cx}" y="${cy - r - sw - 4}" text-anchor="middle" fill="#8892a4"
        font-size="${size * 0.085}" font-family="system-ui,sans-serif">${label}</text>
  <!-- min/max -->
  <text x="${cx - r}" y="${cy + 6}" text-anchor="middle" fill="#8892a4"
        font-size="${size * 0.075}" font-family="system-ui,sans-serif">${min}</text>
  <text x="${cx + r}" y="${cy + 6}" text-anchor="middle" fill="#8892a4"
        font-size="${size * 0.075}" font-family="system-ui,sans-serif">${max}</text>
</svg>`;
}

function makeSmallGaugeSVG(value, min, max, unit) {
  return makeGaugeSVG(value, min, max, '', unit, 90);
}

// ── Status helpers ────────────────────────────────────────────────────────────

function statusColor(s) {
  if (s === 'ok') return 'var(--ok)';
  if (s === 'warning') return 'var(--warn)';
  if (s === 'critical') return 'var(--crit)';
  return 'var(--text2)';
}

function statusClass(s) {
  if (s === 'ok')                      return 'ok';
  if (s === 'warning')                 return 'warning';
  if (s === 'critical' || s === 'error') return 'critical';
  if (s === 'offline')                 return 'offline';
  return '';
}

function aggStatus(device) {
  return device.aggregate_status || device.connection_status || 'ok';
}

// ── Card rendering ────────────────────────────────────────────────────────────

const CARD_PARAMS = [
  { key: 'engine_rpm',         label: 'ЧВД',      unit: 'rpm' },
  { key: 'gen_avg_frequency',  label: 'Частота',   unit: 'Hz'  },
  { key: 'coolant_temp',       label: 'Охл. жид.', unit: '°C'  },
  { key: 'fuel_level',         label: 'Топливо',   unit: '%'   },
  { key: 'battery_voltage',    label: 'АКБ',       unit: 'V'   },
  { key: 'total_percent_kw',   label: 'Нагрузка',  unit: '%'   },
];

function paramVal(params, key) {
  const p = params && params[key];
  if (!p) return null;
  return p;
}

function fmtVal(p) {
  if (!p) return { text: '—', cls: 'no-data', title: '' };
  if (p.status === 'fid')   return { text: 'N/A',  cls: 'fid',   title: 'Датчик не подключён (FID)' };
  if (p.status === 'error') return { text: 'ERR',  cls: 'error', title: p.error || 'Ошибка чтения' };
  if (p.value === null || p.value === undefined) return { text: '—', cls: 'no-data', title: '' };
  const v = p.value;
  const text = v < 10 ? v.toFixed(2) : v.toFixed(1);
  return { text, cls: statusClass(p.status), title: '' };
}

function renderCard(name, device) {
  const status = aggStatus(device);
  const params = device.parameters || {};
  const isOffline = status === 'offline';

  // Small gauges for RPM and frequency
  const rpm = paramVal(params, 'engine_rpm');
  const freq = paramVal(params, 'gen_avg_frequency');
  const rpmVal = (rpm && rpm.value !== null) ? rpm.value : 0;
  const freqVal = (freq && freq.value !== null) ? freq.value : 0;

  const gaugesHtml = isOffline ? '' : `
    <div class="card-gauges">
      <div class="gauge-wrap">
        ${makeSmallGaugeSVG(rpmVal, 0, 2000, 'rpm')}
        <div class="gauge-label">ЧВД</div>
      </div>
      <div class="gauge-wrap">
        ${makeSmallGaugeSVG(freqVal, 0, 60, 'Hz')}
        <div class="gauge-label">Частота</div>
      </div>
    </div>`;

  const metricsHtml = CARD_PARAMS.filter(p => p.key !== 'engine_rpm' && p.key !== 'gen_avg_frequency')
    .map(p => {
      const param = paramVal(params, p.key);
      const { text, cls, title } = fmtVal(param);
      const unit = (param && param.unit) ? param.unit : p.unit;
      const titleAttr = title ? ` title="${title}"` : '';
      return `<div class="metric-item">
        <span class="metric-label">${p.label}</span>
        <span class="metric-value ${cls}"${titleAttr}>${text} <small>${unit}</small></span>
      </div>`;
    }).join('');

  const errBanner = isOffline
    ? `<div style="color:#94a3b8;font-size:12px;margin-bottom:8px;">📡 Нет связи с устройством</div>` : '';

  const summary = device.summary || {};
  const footerText = isOffline
    ? 'Устройство недоступно'
    : `ok: ${summary.ok || 0}  fid: ${summary.fid || 0}  err: ${summary.errors || 0}`;

  const dotClass = status === 'ok'       ? 'dot-ok'
                 : status === 'warning'  ? 'dot-warning'
                 : status === 'offline'  ? 'dot-offline'
                 : 'dot-critical';

  const cardClass = statusClass(status);

  return `<div class="device-card ${cardClass}" data-device="${name}" role="button" tabindex="0">
  <div class="card-header">
    <span class="card-name">${name}</span>
    <span class="card-status-dot ${dotClass}"></span>
  </div>
  ${errBanner}
  ${isOffline ? '' : gaugesHtml}
  <div class="card-metrics">${metricsHtml}</div>
  <div class="card-footer">
    <span>${footerText}</span>
    <span style="color:var(--blue);font-size:11px;">Подробнее →</span>
  </div>
</div>`;
}

// ── Drawer rendering ──────────────────────────────────────────────────────────

const BAR_PARAMS = [
  { key: 'fuel_level',        label: 'Уровень топлива', min: 0, max: 100 },
  { key: 'coolant_temp',      label: 'Температура охл.жид.', min: 40, max: 110 },
  { key: 'battery_voltage',   label: 'Напряжение АКБ',  min: 10, max: 15 },
  { key: 'total_percent_kw',  label: 'Нагрузка %',      min: 0, max: 100 },
  { key: 'engine_oil_pressure',label: 'Давление масла', min: 0, max: 500 },
];

const ALL_PARAM_LABELS = {
  battery_voltage:    'Напряжение АКБ',
  gen_avg_frequency:  'Частота генератора',
  gen_voltage:        'Напряжение генератора',
  coolant_temp:       'Температура охл.жид.',
  engine_oil_pressure:'Давление масла',
  engine_rpm:         'ЧВД',
  total_percent_kw:   'Нагрузка %',
  fuel_level:         'Уровень топлива',
  engine_oil_level:   'Уровень масла',
  fuel_consumption:   'Расход топлива',
  energy_kwh:         'Электроэнергия',
  energy_kvarh:       'Реактивная энергия',
  engine_hours:       'Моточасы',
};

function renderDrawer(name, device) {
  const params = device.parameters || {};
  const status = aggStatus(device);
  const isOffline = status === 'offline';

  // Big gauges
  const rpmP = paramVal(params, 'engine_rpm');
  const loadP = paramVal(params, 'total_percent_kw');
  const rpmVal  = (rpmP  && rpmP.value  !== null) ? rpmP.value  : 0;
  const loadVal = (loadP && loadP.value !== null) ? loadP.value : 0;

  const bigGauges = isOffline ? '' : `
    <div class="drawer-gauges">
      <div class="gauge-wrap">
        ${makeGaugeSVG(rpmVal, 0, 2000, 'ЧВД', 'rpm', 160)}
        <div class="gauge-label">Обороты двигателя</div>
      </div>
      <div class="gauge-wrap">
        ${makeGaugeSVG(loadVal, 0, 100, '% мощн.', '%', 160)}
        <div class="gauge-label">Нагрузка</div>
      </div>
    </div>`;

  const errBanner = isOffline
    ? `<div class="connect-error-banner" style="background:rgba(100,116,139,.12);border-color:#64748b;color:#94a3b8;">📡 Устройство недоступно<br><small>${device.connect_error || 'нет связи'}</small></div>`
    : '';

  // Bars
  const barsHtml = isOffline ? '' : BAR_PARAMS.map(bp => {
    const p = paramVal(params, bp.key);
    if (!p || p.value === null || p.status === 'fid') return '';
    const frac = Math.max(0, Math.min(1, (p.value - bp.min) / (bp.max - bp.min)));
    const pct = (frac * 100).toFixed(1);
    const cls = statusClass(p.status);
    return `<div class="bar-row">
      <div class="bar-label">
        <span>${bp.label}</span>
        <span style="color:${statusColor(p.status)}">${fmtVal(p).text} ${p.unit}</span>
      </div>
      <div class="bar-track"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>
    </div>`;
  }).join('');

  // Full table
  const tableRows = Object.entries(params).map(([key, p]) => {
    const label = ALL_PARAM_LABELS[key] || p.title || key;
    const { text, title } = fmtVal(p);
    const dotColor = p.status === 'fid' ? 'var(--text2)'
                   : p.status === 'error' ? 'var(--crit)'
                   : statusColor(p.status);
    const titleAttr = title ? ` title="${title}"` : '';
    const noteHtml = p.status === 'fid'
      ? ` <span style="color:var(--text2);font-size:10px">(не подкл.)</span>`
      : p.status === 'error'
      ? ` <span style="color:var(--crit);font-size:10px" title="${p.error || ''}">(ошибка)</span>`
      : '';
    return `<tr>
      <td><span class="param-status-dot" style="background:${dotColor}"></span>${label}</td>
      <td${titleAttr}><strong>${text}</strong> ${p.unit || ''}${noteHtml}</td>
    </tr>`;
  }).join('');

  const slaveInfo = `slave_id=${device.slave_id}  |  conn: ${device.connection_status}`;

  return `
    <div class="drawer-title">${name}</div>
    <div class="drawer-subtitle">${slaveInfo}</div>
    ${errBanner}
    ${bigGauges}
    <div class="drawer-bars">${barsHtml}</div>
    <table class="param-table">
      <thead><tr><th>Параметр</th><th>Значение</th></tr></thead>
      <tbody>${tableRows}</tbody>
    </table>`;
}

// ── State & update loop ───────────────────────────────────────────────────────

let _lastSnapshot = null;
let _openDevice = null;

function updateOverview(snapshot) {
  const grid = document.getElementById('overview-grid');
  const devices = snapshot.devices || {};
  const names = Object.keys(devices);

  if (names.length === 0) {
    grid.innerHTML = '<div class="loading-placeholder">Нет устройств в данных</div>';
    return;
  }

  // Re-render cards (preserve scroll)
  grid.innerHTML = names.map(n => renderCard(n, devices[n])).join('');

  // Bind click events
  grid.querySelectorAll('.device-card').forEach(card => {
    const name = card.dataset.device;
    card.addEventListener('click', () => openDrawer(name, devices[name]));
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') openDrawer(name, devices[name]);
    });
  });
}

function updateDrawer(snapshot) {
  if (!_openDevice) return;
  const device = (snapshot.devices || {})[_openDevice];
  if (!device) return;
  document.getElementById('drawer-content').innerHTML = renderDrawer(_openDevice, device);
}

function openDrawer(name, device) {
  _openDevice = name;
  document.getElementById('drawer-content').innerHTML = renderDrawer(name, device);
  document.getElementById('drawer').classList.remove('hidden');
  document.getElementById('drawer-overlay').classList.remove('hidden');
}

function closeDrawer() {
  _openDevice = null;
  document.getElementById('drawer').classList.add('hidden');
  document.getElementById('drawer-overlay').classList.add('hidden');
}

function updateHeader(snapshot) {
  const modeBadge = document.getElementById('mode-badge');
  const mode = snapshot.mode || 'unknown';
  modeBadge.textContent = mode === 'demo' ? 'DEMO' : mode === 'real' ? 'REAL' : mode;
  modeBadge.className = 'mode-badge ' + mode;

  const ts = snapshot.timestamp;
  if (ts) {
    const d = new Date(ts);
    const age = snapshot.cache_age_s;
    const ageStr = (age !== undefined && age > 0) ? ` (данные ${age}с назад)` : '';
    document.getElementById('last-update').textContent =
      d.toLocaleTimeString('ru-RU') + ageStr;
  }

  // Connection indicator
  const sum = snapshot.summary || {};
  const dot = document.getElementById('conn-indicator');
  dot.className = 'conn-dot';
  if (snapshot.error) {
    dot.classList.add('conn-error');
  } else if (sum.successful_devices === sum.total_devices && sum.total_devices > 0) {
    dot.classList.add('conn-ok');
  } else if (sum.successful_devices === 0) {
    dot.classList.add('conn-error');
  } else {
    dot.classList.add('conn-warn');
  }
}

async function fetchAndRender() {
  try {
    const resp = await fetch('/api/snapshot');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const snapshot = await resp.json();
    _lastSnapshot = snapshot;
    updateHeader(snapshot);

    // Warming up — первый опрос ещё не завершён
    if (snapshot.status === 'warming_up') {
      const grid = document.getElementById('overview-grid');
      grid.innerHTML = `<div class="loading-placeholder">
        ⏳ ${snapshot.message || 'Идёт первый опрос устройств...'}
      </div>`;
      return;
    }

    updateOverview(snapshot);
    updateDrawer(snapshot);
  } catch (e) {
    console.error('Fetch error:', e);
    document.getElementById('conn-indicator').className = 'conn-dot conn-error';
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.getElementById('drawer-close').addEventListener('click', closeDrawer);
document.getElementById('drawer-overlay').addEventListener('click', closeDrawer);

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeDrawer();
});

fetchAndRender();
setInterval(fetchAndRender, refreshMs);
