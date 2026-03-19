/* PowerWizard Monitor — app.js */
"use strict";

const refreshMs = 5000;

// ── Gauges ────────────────────────────────────────────────────────────────────
// Same zone proportions for both gauges (per customer request).
// RPM max = 8031.875 (full register range). At 1500 rpm → 18.7% → deep green.

const GAUGE_DEFS = {
  rpm:  { zones:[{to:.70,c:'#27ae60'},{to:.85,c:'#e67e22'},{to:1,c:'#c0392b'}], min:0, max:8032, unit:'rpm' },
  load: { zones:[{to:.70,c:'#27ae60'},{to:.85,c:'#e67e22'},{to:1,c:'#c0392b'}], min:0, max:100,  unit:'%'   },
};

function arc(cx, cy, r, a0deg, a1deg) {
  const a0 = a0deg*Math.PI/180, a1 = a1deg*Math.PI/180;
  const large = a1deg-a0deg > 180 ? 1 : 0;
  return `M${(cx+r*Math.cos(a0)).toFixed(2)},${(cy+r*Math.sin(a0)).toFixed(2)}`
       + `A${r},${r},0,${large},1,${(cx+r*Math.cos(a1)).toFixed(2)},${(cy+r*Math.sin(a1)).toFixed(2)}`;
}

function makeGauge(value, def, size) {
  size = size || 130;
  const cx = size/2, cy = size*.56, r = size*.36, sw = size*.075;
  const frac = Math.max(0, Math.min(1, (value-def.min)/(def.max-def.min)));

  let zones = '';
  let prev = 0;
  for (const z of def.zones) {
    if (z.to <= prev) continue;
    zones += `<path d="${arc(cx,cy,r,180+prev*180,180+z.to*180)}" fill="none" stroke="${z.c}" stroke-width="${sw}" stroke-linecap="butt"/>`;
    prev = z.to;
  }

  const ndeg = (180+frac*180)*Math.PI/180;
  const nl = r-1, nx = cx+nl*Math.cos(ndeg), ny = cy+nl*Math.sin(ndeg);
  const ox = cx+5*Math.cos(ndeg), oy = cy+5*Math.sin(ndeg);

  const disp = isNaN(value)?'—': value>=1000?value.toFixed(0): value<10?value.toFixed(2):value.toFixed(1);
  const valFs  = Math.round(size*.13);
  const unitFs = Math.round(size*.08);
  const lblFs  = Math.max(7, Math.round(size*.068));

  // min label: left arc tip, max label: right arc tip
  // Place them BELOW the value text so they don't overlap
  const valY  = Math.round(cy + sw/2 + 13);         // main value y
  const lblY  = valY + valFs + 1;                    // labels just below value
  const minX  = Math.max(lblFs+1, cx - r);
  const maxX  = Math.min(size - lblFs-1, cx + r);

  // Format max label: show full integer (8032 not 8k)
  const fmtMax = v => v >= 1000 ? Math.round(v).toString() : String(v);
  const minLbl = '0';
  const maxLbl = fmtMax(def.max);

  const h = lblY + lblFs + 3;

  return `<svg width="${size}" height="${h}" viewBox="0 0 ${size} ${h}" xmlns="http://www.w3.org/2000/svg">
  <path d="${arc(cx,cy,r,180,360)}" fill="none" stroke="#1c2234" stroke-width="${sw}" stroke-linecap="round"/>
  ${zones}
  <line x1="${ox.toFixed(1)}" y1="${oy.toFixed(1)}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}"
        stroke="#dde6f5" stroke-width="${(sw*.28).toFixed(1)}" stroke-linecap="round"/>
  <text x="${cx}" y="${valY}" text-anchor="middle"
        fill="#dde6f5" font-size="${valFs}" font-weight="700"
        font-family="'JetBrains Mono',monospace">${disp}<tspan font-size="${unitFs}" fill="#8fa8cc" dx="2">${def.unit}</tspan></text>
  <text x="${minX.toFixed(1)}" y="${lblY}" text-anchor="middle"
        fill="#6688aa" font-size="${lblFs}" font-family="monospace">${minLbl}</text>
  <text x="${maxX.toFixed(1)}" y="${lblY}" text-anchor="middle"
        fill="#6688aa" font-size="${lblFs}" font-family="monospace">${maxLbl}</text>
</svg>`;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function sCls(s) {
  if (s==='ok')                    return 'ok';
  if (s==='warning')               return 'warning';
  if (s==='critical'||s==='error') return 'critical';
  if (s==='offline')               return 'offline';
  return 'fid';
}
function agg(d)  { return d.aggregate_status || d.connection_status || 'ok'; }
function pv(p,k) { return (p&&p[k])||null; }

function fmt(p) {
  if (!p)               return {text:'—',   cls:'fid'};
  if (p.status==='fid') return {text:'N/A', cls:'fid'};
  if (p.status==='error')return{text:'ERR', cls:'error'};
  if (p.value==null)    return {text:'—',   cls:'fid'};
  const v=p.value, t=v>=1000?v.toFixed(0):v<10?v.toFixed(2):v.toFixed(1);
  return {text:t, cls:sCls(p.status)};
}

function pillHtml(st) {
  const lbl={ok:'normal',warning:'warning',critical:'alarm',offline:'offline'}[st]||st;
  const cls={ok:'pill-ok',warning:'pill-warning',critical:'pill-critical',offline:'pill-offline'}[st]||'pill-offline';
  return `<span class="status-pill ${cls}">${lbl}</span>`;
}

// ── Bar helper ────────────────────────────────────────────────────────────────
function barRow(params, def, cls_pfx) {
  const p=pv(params,def.key), {text,cls}=fmt(p);
  const valid=p&&p.value!=null&&p.status!=='fid'&&p.status!=='error';
  const frac=valid?Math.max(0,Math.min(1,(p.value-def.min)/(def.max-def.min))):0;
  const valStr=valid?`${text} ${p.unit||def.unit}`:text;
  const cp=cls_pfx||'cbar';
  return `<div class="${cp}">
    <span class="${cp}-lbl">${def.label}</span>
    <div class="${cp}-trk"><div class="${cp}-fil ${cls}" style="width:${(frac*100).toFixed(1)}%"></div></div>
    <span class="${cp}-val ${cls}">${valStr}</span>
  </div>`;
}

// ── Density ───────────────────────────────────────────────────────────────────
// 2 DGUs  → mode-2 : two large horizontal cards (gauge left + bars right)
// any other count → mode-3 : compact cards, 4 per row, wrapping to new rows
function getMode(n) {
  if (n === 2) return 'mode-2';
  return 'mode-3';
}

// ── Parameter definitions ─────────────────────────────────────────────────────
// Bars: with progress track
const BARS_FULL = [
  {key:'fuel_level',         label:'Топливо',        min:0,  max:100, unit:'%'},
  {key:'battery_voltage',    label:'АКБ',             min:10, max:15,  unit:'V'},
  {key:'coolant_temp',       label:'Темп. ОЖ',        min:40, max:110, unit:'°C'},
  {key:'gen_voltage',        label:'Напряжение ген.', min:0,  max:500, unit:'V'},
  {key:'gen_avg_frequency',  label:'Частота',         min:45, max:55,  unit:'Hz'},
  {key:'total_percent_kw',   label:'Нагрузка %',      min:0,  max:100, unit:'%'},
  {key:'engine_oil_pressure',label:'Давл. масла',     min:0,  max:500, unit:'kPa'},
];
const BARS_KEY = BARS_FULL.slice(0,6);   // без масла
const BARS_MAIN = BARS_FULL.slice(0,5);  // без нагрузки и масла

// Compact key-value params (2 columns)
const PARAMS_OPS = [
  {key:'gen_avg_frequency',  label:'Частота',  unit:'Hz'},
  {key:'gen_voltage',        label:'Напряж.',  unit:'V'},
  {key:'total_percent_kw',   label:'Нагрузка', unit:'%'},
  {key:'coolant_temp',       label:'Темп.ОЖ',  unit:'°C'},
  {key:'battery_voltage',    label:'АКБ',      unit:'V'},
  {key:'fuel_level',         label:'Топливо',  unit:'%'},
];
const PARAMS_DENSE = [
  {key:'engine_rpm',         label:'ЧВД',       unit:'rpm'},
  {key:'gen_avg_frequency',  label:'Частота',   unit:'Hz'},
  {key:'gen_voltage',        label:'Напряж.',   unit:'V'},
  {key:'total_percent_kw',   label:'Нагрузка',  unit:'%'},
  {key:'coolant_temp',       label:'Темп.ОЖ',   unit:'°C'},
  {key:'battery_voltage',    label:'АКБ',       unit:'V'},
  {key:'fuel_level',         label:'Топливо',   unit:'%'},
];
const ACCUM = [
  {key:'engine_hours', label:'Моточасы',      unit:'h'},
  {key:'energy_kwh',   label:'Электроэнергия', unit:'kWh'},
  {key:'energy_kvarh', label:'Реакт. энергия', unit:'kVArh'},
];

// Compact 2-col key=value cell
function cp(params, def) {
  const p=pv(params,def.key), {text,cls}=fmt(p);
  const u=(p&&p.status!=='fid'&&p.status!=='error'&&p.unit)?p.unit:def.unit;
  const showU=text!=='—'&&text!=='N/A'&&text!=='ERR';
  return `<div class="cp"><span class="cp-lbl">${def.label}</span>`
       + `<span class="cp-val ${cls}">${text}${showU?`&thinsp;<small style="color:var(--tx3)">${u}</small>`:''}</span></div>`;
}

// Compact accum strip (inline counters)
function accumStrip(params) {
  return `<div class="card-accum">${ACCUM.map(d=>{
    const p=pv(params,d.key), {text,cls}=fmt(p);
    const u=(p&&p.status!=='fid'&&p.status!=='error'&&p.unit)?p.unit:d.unit;
    return `<div class="ca-item">
      <span class="ca-lbl">${d.label}</span>
      <span class="ca-val ${cls}">${text}${text!=='—'&&text!=='N/A'?`&thinsp;<small style="color:var(--tx3)">${u}</small>`:''}</span>
    </div>`;
  }).join('')}</div>`;
}

// ── renderCard ────────────────────────────────────────────────────────────────
//
// Layout philosophy:
//   mode-1 (1 DGU):  horizontal: [two gauges] | [all bars right column] + accum strip
//   mode-2 (2 DGUs): horizontal: [one gauge]  | [key bars right column] + accum strip
//   mode-3 (3-4):    vertical: gauge → key bars → compact accum strip
//   mode-mid (5-6):  vertical: small gauge → 2-col params (ops only)
//   mode-dense (7+): vertical: 2-col params (ops + rpm, no gauge)

function renderCard(name, device, mode, count) {
  mode  = mode  || 'mode-mid';
  count = count || 1;
  const st=agg(device), params=device.parameters||{}, isOff=st==='offline';
  const cardCls=sCls(st);
  const rpmV=(pv(params,'engine_rpm')?.value)||0;
  const ldV =(pv(params,'total_percent_kw')?.value)||0;

  let body='';

  if (isOff) {
    body=`<div class="card-offline-msg">📡 Нет связи с устройством</div>`;

  } else if (mode==='mode-1') {
    // Horizontal: gauges left column | bars right column, accum strip below
    const gaugesCol = `
      <div class="card-gauges-col">
        <div class="cg-cell"><div class="cg-title">ЧВД</div>${makeGauge(rpmV,GAUGE_DEFS.rpm,160)}</div>
        <div class="cg-cell"><div class="cg-title">% мощности</div>${makeGauge(ldV,GAUGE_DEFS.load,160)}</div>
      </div>`;
    const barsCol = `<div class="card-bars-col">${BARS_FULL.map(d=>barRow(params,d,'cbar')).join('')}</div>`;
    body = `<div class="card-body-h">${gaugesCol}${barsCol}</div>${accumStrip(params)}`;

  } else if (mode==='mode-2') {
    // Horizontal: one gauge left | key bars right, accum strip below
    const gaugeCol = `
      <div class="card-gauges-col">
        <div class="cg-cell"><div class="cg-title">ЧВД</div>${makeGauge(rpmV,GAUGE_DEFS.rpm,140)}</div>
        <div class="cg-cell" style="margin-top:6px"><div class="cg-title">% мощн.</div>${makeGauge(ldV,GAUGE_DEFS.load,140)}</div>
      </div>`;
    const barsCol = `<div class="card-bars-col">${BARS_KEY.map(d=>barRow(params,d,'cbar')).join('')}</div>`;
    body = `<div class="card-body-h">${gaugeCol}${barsCol}</div>${accumStrip(params)}`;

  } else if (mode==='mode-3') {
    // 3-4 DGUs: two small gauges side-by-side, then bars, then accum strip
    const gaugesRow = `<div class="card-gauges-row">
      <div class="cg-cell"><div class="cg-title">ЧВД</div>${makeGauge(rpmV,GAUGE_DEFS.rpm,95)}</div>
      <div class="cg-cell"><div class="cg-title">% мощн.</div>${makeGauge(ldV,GAUGE_DEFS.load,95)}</div>
    </div>`;
    body = gaugesRow
      + `<div class="card-bars-col">${BARS_KEY.map(d=>barRow(params,d,'cbar')).join('')}</div>`
      + accumStrip(params);

  } else if (mode==='mode-mid') {
    // Vertical: small gauge → 2-col ops params
    body = `
      <div class="card-gauge-row">${makeGauge(rpmV,GAUGE_DEFS.rpm,104)}</div>
      <div class="card-params no-top-border">${PARAMS_OPS.map(d=>cp(params,d)).join('')}</div>`;

  } else {
    // Dense: no gauge, 2-col compact params
    body = `<div class="card-params no-top-border">${PARAMS_DENSE.map(d=>cp(params,d)).join('')}</div>`;
  }

  return `<div class="device-card ${cardCls} ${mode}" data-device="${name}" role="button" tabindex="0">
  <div class="card-top">
    <div><div class="card-name">${name}</div><div class="card-slave">slave_id=${device.slave_id}</div></div>
    ${pillHtml(st)}
  </div>
  ${body}
</div>`;
}

// ── Drawer ────────────────────────────────────────────────────────────────────
const DRAWER_BARS = [
  {key:'fuel_level',         label:'Топливо',        min:0,  max:100, unit:'%'},
  {key:'battery_voltage',    label:'АКБ',             min:10, max:15,  unit:'V'},
  {key:'coolant_temp',       label:'Темп. ОЖ',        min:40, max:110, unit:'°C'},
  {key:'gen_voltage',        label:'Напряжение ген.', min:0,  max:500, unit:'V'},
  {key:'gen_avg_frequency',  label:'Частота',         min:45, max:55,  unit:'Hz'},
  {key:'total_percent_kw',   label:'Нагрузка %',      min:0,  max:100, unit:'%'},
];

const PARAM_GROUPS = [
  {label:'Текущие параметры', keys:['engine_rpm','gen_avg_frequency','gen_voltage','coolant_temp','engine_oil_pressure','battery_voltage','total_percent_kw']},
  {label:'Уровни',            keys:['fuel_level','engine_oil_level','cooldown_remaining']},
  {label:'Накопительные',     keys:['energy_kwh','energy_kvarh','engine_hours']},
];
const PARAM_LABELS = {
  battery_voltage:'Напряжение АКБ', gen_avg_frequency:'Частота генератора',
  gen_voltage:'Выходное напряжение', coolant_temp:'Температура охл. жидкости',
  engine_oil_pressure:'Давление масла', engine_rpm:'ЧВД',
  total_percent_kw:'Процент потреб. мощности', fuel_level:'Уровень топлива',
  engine_oil_level:'Уровень масла', energy_kwh:'Электроэнергия',
  energy_kvarh:'Реактивная энергия', engine_hours:'Моточасы',
  cooldown_remaining:'Время до конца охлаждения',
};

function drawerParamRow(key, p) {
  const lbl=PARAM_LABELS[key]||p.title||key;
  const {text}=fmt(p);
  const st=p.status==='fid'?'fid':p.status==='error'?'error':(p.status||'ok');
  const stl={ok:'ok',warning:'warn',critical:'crit',fid:'N/A',error:'ERR'}[st]||st;
  const tip=p.error?` title="${p.error}"`:p.status==='fid'?' title="Датчик не подключён"':'';
  return `<tr${tip}><td>${lbl}</td><td>${text}</td><td style="color:var(--tx3)">${p.unit||''}</td><td><span class="p-st ${st}">${stl}</span></td></tr>`;
}

function renderDrawerBody(name, device) {
  const params=device.parameters||{}, st=agg(device), isOff=st==='offline';

  document.getElementById('d-title').textContent=name;
  document.getElementById('d-sub').textContent=`slave_id=${device.slave_id}  |  ${device.connection_status}`;
  const ok=device.connection_status==='ok';
  document.getElementById('d-conn-dot').className='conn-dot '+(ok?'conn-ok':'conn-error');
  document.getElementById('d-conn-txt').textContent='Связь: '+(ok?'ok':'нет');

  if (isOff) return `<div class="offline-banner">📡 Устройство недоступно<br>
    <small style="color:var(--tx3)">${device.connect_error||'нет связи'}</small></div>`;

  const rV=(pv(params,'engine_rpm')?.value)||0;
  const lV=(pv(params,'total_percent_kw')?.value)||0;

  const gaugesHtml=`<div class="d-gauges">
    <div class="d-gc"><div class="d-gc-title">ЧВД</div>${makeGauge(rV,GAUGE_DEFS.rpm,185,'0 — 8032 rpm')}</div>
    <div class="d-gc"><div class="d-gc-title">% мощности</div>${makeGauge(lV,GAUGE_DEFS.load,185,'0 — 100 %')}</div>
  </div>`;

  const barsHtml=`<div class="d-bars">${DRAWER_BARS.map(d=>barRow(params,d,'d-bar')).join('')}</div>`;

  const used=new Set();
  let tbody='';
  PARAM_GROUPS.forEach(g=>{
    const rows=g.keys.filter(k=>params[k]).map(k=>{used.add(k);return drawerParamRow(k,params[k]);}).join('');
    if(rows) tbody+=`<tr class="param-group-row"><td colspan="4">${g.label}</td></tr>${rows}`;
  });
  const extra=Object.entries(params).filter(([k])=>!used.has(k)).map(([k,p])=>drawerParamRow(k,p)).join('');
  if(extra) tbody+=`<tr class="param-group-row"><td colspan="4">Прочее</td></tr>${extra}`;

  return gaugesHtml+barsHtml+
    `<div class="d-tbl-title">Все параметры</div>
     <table class="param-table">
       <thead><tr><th>Параметр</th><th>Значение</th><th>Ед.</th><th>Статус</th></tr></thead>
       <tbody>${tbody}</tbody>
     </table>`;
}

// ── State & loop ───────────────────────────────────────────────────────────────
let _last=null, _open=null, _fly=false;

function updateOverview(snap) {
  const grid=document.getElementById('overview-grid');
  const devs=snap.devices||{}, names=Object.keys(devs);
  if(!names.length){grid.className='overview-grid';grid.innerHTML='<div class="loading-placeholder">Нет устройств</div>';return;}
  const mode=getMode(names.length);
  grid.className='overview-grid '+mode;
  grid.innerHTML=names.map(n=>renderCard(n,devs[n],mode,names.length)).join('');
  grid.querySelectorAll('.device-card').forEach(c=>{
    const n=c.dataset.device;
    c.addEventListener('click',()=>openDrawer(n,devs[n]));
    c.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')openDrawer(n,devs[n]);});
  });
}

function updateDrawer(snap){if(!_open)return;const d=(snap.devices||{})[_open];if(!d)return;document.getElementById('d-body').innerHTML=renderDrawerBody(_open,d);}
function openDrawer(n,d){_open=n;document.getElementById('d-body').innerHTML=renderDrawerBody(n,d);document.getElementById('drawer').classList.remove('hidden');document.getElementById('drawer-overlay').classList.remove('hidden');}
function closeDrawer(){_open=null;document.getElementById('drawer').classList.add('hidden');document.getElementById('drawer-overlay').classList.add('hidden');}

function updateHeader(snap) {
  const mode=snap.mode||'unknown';
  const b=document.getElementById('mode-badge');
  b.textContent=mode==='demo'?'DEMO':mode==='real'?'REAL':mode;
  b.className='mode-badge '+mode;
  const sum=snap.summary||{}, bs=snap.backend_status;
  const ts=snap.timestamp?new Date(snap.timestamp).toLocaleTimeString('ru-RU'):'—';
  const bss=bs==='warming_up'?' | первый опрос...':bs==='error'?' | ошибка backend':'';
  document.getElementById('header-sub').textContent=`Обновление: ${ts} | режим: ${mode} | устройств: ${sum.successful_devices||0}/${sum.total_devices||0}${bss}`;
  const dot=document.getElementById('conn-indicator');
  dot.className='conn-dot';
  if(bs==='warming_up') dot.classList.add('conn-warn');
  else if(bs==='error') dot.classList.add('conn-error');
  else if((sum.successful_devices||0)===(sum.total_devices||0)&&(sum.total_devices||0)>0) dot.classList.add('conn-ok');
  else if((sum.successful_devices||0)===0) dot.classList.add('conn-error');
  else dot.classList.add('conn-warn');
}

async function fetchAndRender() {
  if(_fly) return; _fly=true;
  try {
    const r=await fetch('/api/snapshot');
    if(!r.ok) throw new Error('HTTP '+r.status);
    const snap=await r.json(); _last=snap;
    updateHeader(snap);
    const bs=snap.backend_status;
    if(bs==='warming_up'){document.getElementById('overview-grid').innerHTML='<div class="loading-placeholder">⏳ Идёт первый опрос оборудования...</div>';return;}
    if(bs==='error'){document.getElementById('overview-grid').innerHTML=`<div class="loading-placeholder" style="color:var(--cr-t)">⚠ Ошибка backend: ${snap.error||'?'}</div>`;return;}
    updateOverview(snap); updateDrawer(snap);
  } catch(e){console.error(e);document.getElementById('conn-indicator').className='conn-dot conn-error';}
  finally{_fly=false;}
}

document.getElementById('drawer-close').addEventListener('click',closeDrawer);
document.getElementById('drawer-overlay').addEventListener('click',closeDrawer);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});
fetchAndRender();
setInterval(fetchAndRender,refreshMs);
