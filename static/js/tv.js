// ── TV Wall Dashboard ─────────────────────────────────────────────────────────

const VERSE_INTERVAL       = 45000; // rotate verse every 45s
const ATTENDANCE_INTERVAL  =  5000; // attendance refreshes every 5s (near-live clock-in)
const KANBAN_INTERVAL      = 10000; // kanban refreshes every 10s

// Client → border color (matches dashboard + kanban)
const CLIENT_COLORS = {
  'Byron Digital': '#2563eb',
  'Byron':         '#2563eb',
  'Pej':           '#7c3aed',
  'CHD':           '#ea580c',
  'Waren Digital': '#0891b2',
  'Syllabi':       '#db2777',
  'MBQ':           '#16a34a',
  'Internal':      '#64748b',
};
function clientColor(c) { return CLIENT_COLORS[c] || '#3b82f6'; }

// ── Live clock ────────────────────────────────────────────────────────────────
function tickClock() {
  const now = new Date();
  const timeEl = document.getElementById('tv-clock');
  const dateEl = document.getElementById('tv-date');
  if (timeEl) timeEl.textContent = now.toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true
  });
  if (dateEl) dateEl.textContent = now.toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
  });
}
setInterval(tickClock, 1000);
tickClock();

// ── Fetch and render TV data ──────────────────────────────────────────────────
async function refreshAttendance() {
  try {
    const res = await fetch('/api/tv/data');
    if (!res.ok) return;
    const data = await res.json();
    renderAttendance(data.attendance);
  } catch (e) {
    console.warn('TV attendance refresh failed:', e);
  }
}

async function refreshKanban() {
  try {
    const res = await fetch('/api/tv/data');
    if (!res.ok) return;
    const data = await res.json();
    renderKanban(data.columns);
    if (data.verse) showVerse(data.verse);
  } catch (e) {
    console.warn('TV kanban refresh failed:', e);
  }
}

function renderAttendance(list) {
  const grid = document.getElementById('attendance-grid');
  if (!grid) return;
  grid.innerHTML = list.map(emp => {
    const isIn  = emp.clocked_in;
    const isOut = emp.clocked_out;
    const statusClass = isIn ? 'tv-dot-green' : isOut ? 'tv-dot-gray' : 'tv-dot-idle';
    const statusLabel = isIn ? 'IN' : isOut ? 'OUT' : 'IDLE';
    const inTime  = emp.clock_in_time  ? emp.clock_in_time.slice(0,5)  : '';
    const outTime = emp.clock_out_time ? emp.clock_out_time.slice(0,5) : '';

    let timeHtml = '';
    if (isIn && inTime)  timeHtml = `<span style="font-size:.6rem;color:#4ade80;">In&nbsp;${inTime}</span>`;
    if (isOut && outTime) timeHtml = `
      <span style="font-size:.6rem;color:#94a3b8;">In&nbsp;${inTime}</span>
      <span style="font-size:.6rem;color:#94a3b8;">Out&nbsp;${outTime}</span>`;

    // Avatar: real photo or initials
    const avatarHtml = emp.emp_pic
      ? `<img src="/uploads/${escTV(emp.emp_pic)}"
              style="width:38px;height:38px;border-radius:50%;object-fit:cover;border:2px solid rgba(255,255,255,.15);"
              alt="${escTV(emp.name[0])}">`
      : `<div class="tv-emp-avatar">${escTV(emp.name.split(' ').map(w=>w[0]).join('').slice(0,2))}</div>`;

    return `
      <div class="tv-emp-card ${isIn ? 'tv-emp-in' : ''}">
        ${avatarHtml}
        <div class="tv-emp-info">
          <div class="tv-emp-name">${escTV(emp.name)}</div>
          <div class="tv-emp-meta">${escTV(emp.shift_type)} · ${timeHtml || '—'}</div>
        </div>
        <div class="tv-status-badge ${statusClass}">
          <span class="tv-status-dot"></span>${statusLabel}
        </div>
      </div>`;
  }).join('') || '<div style="text-align:center;color:var(--muted);font-size:.8rem;padding:20px">No employees</div>';
}

function renderKanban(columns) {
  if (!columns) return;
  const statuses = ['Todo', 'In Progress', 'For Review', 'Done'];
  statuses.forEach(s => {
    const key     = s.replace(/ /g, '-');
    const countEl = document.getElementById('tv-col-count-' + key);
    const listEl  = document.getElementById('tv-cards-' + key);
    const cards   = columns[s] || [];
    if (countEl) countEl.textContent = cards.length;
    if (!listEl) return;
    if (!cards.length) {
      listEl.innerHTML = '<div style="text-align:center;color:var(--muted);font-size:.7rem;padding:12px">Empty</div>';
      return;
    }
    listEl.innerHTML = cards.map(c => {
      const col = clientColor(c.client);
      // avatar
      const av = c.emp_pic
        ? `<img src="/uploads/${escTV(c.emp_pic)}"
                style="width:18px;height:18px;border-radius:50%;object-fit:cover;border:1.5px solid rgba(255,255,255,.15);flex-shrink:0;">`
        : `<span style="width:18px;height:18px;border-radius:50%;background:rgba(59,130,246,.2);color:#60a5fa;
                         font-size:.55rem;font-weight:800;display:inline-flex;align-items:center;
                         justify-content:center;flex-shrink:0;">${escTV((c.emp_name||'?')[0])}</span>`;
      return `
        <div class="tv-task-card" style="border-left:3px solid ${col}">
          <div class="tv-task-title">${escTV(c.task_title)}</div>
          <div class="tv-task-meta" style="display:flex;align-items:center;gap:5px;margin-top:3px;">
            ${av}
            <span>${escTV(c.emp_name)}</span>
            ${c.client ? `<span style="color:${col};font-size:.6rem;font-weight:700;text-transform:uppercase;">· ${escTV(c.client)}</span>` : ''}
          </div>
          <div class="tv-task-meta">${c.hours_worked ? c.hours_worked+'h' : ''}</div>
        </div>`;
    }).join('');
  });
}

function showVerse(verse) {
  const el = document.getElementById('tv-verse');
  const ref = document.getElementById('tv-verse-ref');
  if (!el) return;
  el.style.opacity = '0';
  setTimeout(() => {
    el.textContent  = '"' + verse.verse + '"';
    if (ref) ref.textContent = '— ' + verse.ref;
    el.style.opacity = '1';
  }, 500);
}

// ── Weather via wttr.in ───────────────────────────────────────────────────────
async function loadWeather() {
  const el = document.getElementById('tv-weather');
  if (!el) return;
  try {
    const res = await fetch('https://wttr.in/?format=j1', { signal: AbortSignal.timeout(5000) });
    const data = await res.json();
    const curr = data.current_condition[0];
    const temp = curr.temp_C;
    const desc = curr.weatherDesc[0].value;
    const humid = curr.humidity;
    el.innerHTML = `<span class="tv-weather-temp">${temp}°C</span>
                    <span class="tv-weather-desc">${escTV(desc)} · ${humid}% humidity</span>`;
  } catch (e) {
    el.textContent = '';
  }
}

function escTV(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Boot ──────────────────────────────────────────────────────────────────────
refreshAttendance();
refreshKanban();
loadWeather();
setInterval(refreshAttendance, ATTENDANCE_INTERVAL);
setInterval(refreshKanban, KANBAN_INTERVAL);
setInterval(loadWeather, 300000);
