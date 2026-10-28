// ── fetch wrapper — always sends X-Requested-With so CSRF middleware skips it ──
const _FETCH_HEADERS = { 'X-Requested-With': 'fetch' };
function apiFetch(url, opts = {}) {
  opts.headers = Object.assign({}, opts.headers || {}, _FETCH_HEADERS);
  return fetch(url, opts);
}

// (live-clock removed — profile avatar in topbar instead)

// ── Flash auto-dismiss ────────────────────────────────────────────────────────
document.querySelectorAll('.flash').forEach(el => {
  setTimeout(() => el.style.opacity = '0', 3500);
  setTimeout(() => el.remove(), 4000);
});

// ── Tab switching ─────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const group = tab.closest('[data-tab-group]');
    const target = tab.dataset.tab;
    group.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    group.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    const content = group.querySelector(`.tab-content[data-tab="${target}"]`);
    if (content) content.classList.add('active');
  });
});

// ── Modal helpers ─────────────────────────────────────────────────────────────
function openModal(id) {
  const overlay = document.getElementById(id);
  if (overlay) overlay.classList.add('open');
}
function closeModal(id) {
  const overlay = document.getElementById(id);
  if (overlay) overlay.classList.remove('open');
}
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.remove('open');
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeCardPanel();
    document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
  }
});

// ── Kanban board ──────────────────────────────────────────────────────────────
let _kanbanReady = false;

function initKanban() {
  if (_kanbanReady) return;
  const cards = document.querySelectorAll('.kanban-card');
  const cols  = document.querySelectorAll('.kanban-col');
  if (!cards.length && !cols.length) return;
  _kanbanReady = true;

  let justDragged = false;

  cards.forEach(card => {
    const isOwn = card.dataset.own === 'true';
    const role  = card.dataset.role;
    const canDrag = BOARD_MANAGER_ROLES.includes(role) || isOwn;

    card.setAttribute('draggable', canDrag ? 'true' : 'false');

    card.addEventListener('dragstart', e => {
      if (!canDrag) { e.preventDefault(); return; }
      e.dataTransfer.setData('text/plain', card.dataset.taskId);
      e.dataTransfer.effectAllowed = 'move';
      justDragged = false;
      setTimeout(() => card.classList.add('dragging'), 0);
    });

    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      justDragged = true;
      setTimeout(() => { justDragged = false; }, 200);
    });

    card.addEventListener('click', e => {
      if (justDragged) return;
      openCardModal(card.dataset.taskId);
    });
  });

  cols.forEach(col => {
    col.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      col.classList.add('drag-over');
    });
    col.addEventListener('dragleave', e => {
      if (!col.contains(e.relatedTarget)) col.classList.remove('drag-over');
    });
    col.addEventListener('drop', async e => {
      e.preventDefault();
      col.classList.remove('drag-over');
      const taskId = e.dataTransfer.getData('text/plain');
      if (!taskId) return;
      const newStatus = col.dataset.status;
      const cardEl = document.querySelector(`.kanban-card[data-task-id="${taskId}"]`);
      if (cardEl && cardEl.dataset.status === newStatus) return;
      moveCard(taskId, newStatus, cardEl ? {
        own: cardEl.dataset.own === 'true',
        person: cardEl.dataset.personName,
        estimate: cardEl.dataset.est,
      } : {});
    });
  });
}

const BOARD_MANAGER_ROLES = ['HR Manager', 'Admin', 'Team Manager'];

// ── Hours prompt (shown before a card moves to For Review / Done) ─────────────
function askHours({ title, message, value, hint }) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay open';
    overlay.style.zIndex = '1000';
    overlay.innerHTML = `
      <div class="modal" style="max-width:360px;" role="dialog" aria-modal="true">
        <div class="modal-header"><span class="modal-title">${esc(title)}</span></div>
        <form class="modal-body" style="display:flex;flex-direction:column;gap:10px;">
          <label class="form-label" for="ask-hours-input" style="margin:0;">${esc(message)}</label>
          <input id="ask-hours-input" type="number" class="form-input" min="0.25" max="200" step="0.25"
                 required value="${value > 0 ? value : ''}" placeholder="e.g. 1.5">
          ${hint ? `<div class="text-xs text-muted">${esc(hint)}</div>` : ''}
          <div class="flex gap-8" style="justify-content:flex-end;margin-top:4px;">
            <button type="button" class="btn btn-outline btn-sm" data-cancel>Cancel</button>
            <button type="submit" class="btn btn-primary btn-sm">Continue</button>
          </div>
        </form>
      </div>`;
    const done = val => { overlay.remove(); document.removeEventListener('keydown', onKey, true); resolve(val); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
    document.addEventListener('keydown', onKey, true);
    overlay.addEventListener('click', e => { if (e.target === overlay) done(null); });
    overlay.querySelector('[data-cancel]').addEventListener('click', () => done(null));
    overlay.querySelector('form').addEventListener('submit', e => {
      e.preventDefault();
      const h = parseFloat(overlay.querySelector('input').value);
      if (h > 0) done(Math.round(h * 100) / 100);
    });
    document.body.appendChild(overlay);
    const input = overlay.querySelector('input');
    input.focus(); input.select();
  });
}

// Moves a card; when the server needs hours for this hand-off, asks for them and retries.
async function moveCard(taskId, status, ctx = {}) {
  const payload = { status };
  try {
    for (let attempt = 0; attempt < 3; attempt++) {
      const res = await apiFetch(`/api/tasks/${taskId}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) { location.reload(); return; }
      let err = {};
      try { err = await res.json(); } catch {}
      if (err.error === 'hours_required') {
        const review = err.kind === 'review';
        const who = ctx.own || !ctx.person ? 'you' : ctx.person;
        const h = await askHours(review ? {
          title: 'Review time',
          message: 'How many hours did you spend reviewing this task?',
        } : {
          title: status === 'Done' ? 'Hours worked' : 'Submit for review',
          message: `How many hours did ${who} work on this task?`,
          value: err.current_hours,
          hint: ctx.estimate ? `Estimated: ${ctx.estimate}h` : '',
        });
        if (h === null) return;
        payload[review ? 'review_hours' : 'hours'] = h;
        continue;
      }
      showToast(res.status === 403 ? 'You can only move your own cards.' : 'Could not move card — please try again.', 'error');
      return;
    }
  } catch {
    showToast('Network error — please refresh.', 'error');
  }
}

// ── Drive file helpers ────────────────────────────────────────────────────────
function _driveFileCard(f, canDelete) {
  const icon = f.file_name.match(/\.(jpg|jpeg|png|gif|webp)$/i) ? '🖼'
             : f.file_name.match(/\.(mp4|mov|avi|webm)$/i) ? '🎬'
             : f.file_name.match(/\.(pdf)$/i) ? '📄'
             : f.file_name.match(/\.(doc|docx)$/i) ? '📝'
             : f.file_name.match(/\.(xls|xlsx)$/i) ? '📊'
             : '📎';
  const size = f.file_size > 1048576 ? (f.file_size/1048576).toFixed(1)+' MB'
             : f.file_size > 1024 ? Math.round(f.file_size/1024)+' KB'
             : (f.file_size||0)+' B';
  return `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:var(--surface-2);border-radius:6px;margin-bottom:4px;">
    <span style="font-size:1.1rem;">${icon}</span>
    <div style="flex:1;min-width:0;">
      <a href="https://drive.google.com/file/d/${f.drive_id}/view" target="_blank"
         style="font-size:.78rem;font-weight:600;color:var(--primary);text-decoration:none;display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${esc(f.file_name)}</a>
      <span style="font-size:.67rem;color:var(--muted);">${size} · ${esc(f.uploaded_by_name||'')} · ${(f.uploaded_at||'').slice(0,10)}</span>
    </div>
    ${canDelete ? `<button onclick="deleteTaskFile(${f.id},this)" style="background:none;border:none;cursor:pointer;color:var(--danger);font-size:.8rem;" title="Delete">✕</button>` : ''}
  </div>`;
}

window.uploadTaskFile = async function(taskId, fileType, inputEl) {
  const file = inputEl.files[0];
  if (!file) return;
  const listId = (fileType === 'input' ? 'input' : 'output') + '-files-list-' + taskId;
  const listEl = document.getElementById(listId);
  const btn = inputEl.previousElementSibling;
  if (btn) btn.textContent = '⏫…';
  const fd = new FormData();
  fd.append('file', file);
  fd.append('file_type', fileType);
  try {
    const res  = await fetch(`/api/tasks/${taskId}/files/upload`, {method:'POST', body:fd, headers:{'X-Requested-With':'fetch'}});
    const data = await res.json();
    if (data.ok) {
      const existing = listEl.querySelector('.text-muted');
      if (existing && existing.textContent.includes('No ')) existing.remove();
      listEl.insertAdjacentHTML('beforeend', _driveFileCard({
        id: data.id, drive_id: data.drive_id, file_name: data.file_name,
        file_size: file.size, uploaded_by_name: data.uploaded_by_name, uploaded_at: new Date().toISOString(),
      }, true));
    } else {
      alert('Upload failed: ' + (data.error || 'Unknown'));
    }
  } catch(e) { alert('Upload error: ' + e.message); }
  finally { if (btn) btn.textContent = '+ Upload'; inputEl.value = ''; }
};

window.deleteTaskFile = async function(fileId, btn) {
  if (!confirm('Delete this file from Drive?')) return;
  const card = btn.closest('[style*="background"]');
  const res = await fetch(`/api/task-files/${fileId}/delete`, {method:'POST', headers:{'X-Requested-With':'fetch','X-CSRFToken': document.querySelector('meta[name=csrf-token]')?.content||''}});
  const data = await res.json();
  if (data.ok && card) card.remove();
  else alert('Delete failed');
};

// ── Card detail modal ─────────────────────────────────────────────────────────
const _activityIcon = {
  created:        '📋',
  status_changed: '↔️',
  reviewer_set:   '👤',
  reviewed:       '✅',
  returned:       '↩️',
  approved:       '🔒',
  archived:       '🗄️',
  unarchived:     '📤',
  hours_updated:  '⏱️',
  commented:      '💬',
  review_hours:   '⏱️',
  estimate_set:   '🎯',
  client_changed: '🏢',
  notes_updated:  '📝',
};
const _statusColor = { 'Todo': 'gray', 'In Progress': 'blue', 'For Review': 'yellow', 'Done': 'green' };

function _buildTimeline(activities, comments, roleByName = {}) {
  const names = Object.keys(roleByName);
  // Latest first — this is the per-task audit trail.
  const items = [
    ...activities.map(a => ({ ...a, _kind: 'event',   _ts: a.created_at })),
    ...comments.map(c  => ({ ...c, _kind: 'comment', _ts: c.timestamp  })),
  ].sort((a, b) => b._ts.localeCompare(a._ts));

  if (!items.length) return '<p class="text-muted text-sm" style="padding:8px 0;">No activity yet.</p>';

  return items.map(item => {
    if (item._kind === 'comment') {
      return `
        <div class="comment">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
            <span class="comment-author">${esc(item.author_name)}${roleTag(roleByName[item.author_name])}</span>
            <span class="comment-time text-xs text-muted">${esc(item._ts)}</span>
          </div>
          <div class="comment-text">${highlightMentions(esc(item.comment_text), names).replace(/\n/g,'<br>')}</div>
        </div>`;
    }
    const icon = _activityIcon[item.activity_type] || '•';
    return `
      <div class="activity-event">
        <span class="activity-icon">${icon}</span>
        <span class="activity-body">
          <strong>${esc(item.actor_name)}</strong>${roleTag(roleByName[item.actor_name])}
          ${item.detail ? esc(item.detail) : item.activity_type.replace('_', ' ')}
        </span>
        <span class="activity-time text-xs text-muted">${esc(item._ts)}</span>
      </div>`;
  }).join('');
}

function openCardPanel() {
  const panel    = document.getElementById('card-panel');
  const backdrop = document.getElementById('card-panel-backdrop');
  if (panel)    panel.classList.add('open');
  if (backdrop) backdrop.classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeCardPanel() {
  const panel    = document.getElementById('card-panel');
  const backdrop = document.getElementById('card-panel-backdrop');
  if (panel)    panel.classList.remove('open');
  if (backdrop) backdrop.classList.remove('open');
  document.body.style.overflow = '';
}
function _closeActiveCardView() {
  if (document.getElementById('card-panel')) closeCardPanel();
  else closeModal('card-modal');
}

async function openCardModal(taskId) {
  const usePanel = !!document.getElementById('card-panel');
  const body = document.getElementById(usePanel ? 'card-panel-body' : 'card-modal-body');
  if (!body) return;

  body.innerHTML = '<div class="empty-state"><div class="emoji">⏳</div><p>Loading…</p></div>';
  if (usePanel) openCardPanel();
  else { const modal = document.getElementById('card-modal'); if (modal) openModal('card-modal'); }

  try {
    const res = await fetch(`/api/tasks/${taskId}/detail`);
    if (!res.ok) { body.innerHTML = '<p class="text-danger" style="padding:16px">Error loading card.</p>'; return; }
    const { card, comments, activities, statuses, user_role, user_name, user_id, all_employees,
            input_files, output_files, clients, can_edit } = await res.json();

    const isOwnCard     = card.emp_id === user_id;
    const canReview     = BOARD_MANAGER_ROLES.includes(user_role);
    const canApprove    = user_role === 'Admin';
    const canPeerReview = user_role === 'Employee' && !isOwnCard && card.status === 'For Review';
    const canMove       = canReview || isOwnCard;
    const canArchive    = canReview || isOwnCard;

    const statusOptions = statuses
      .map(s => `<option value="${s}"${s === card.status ? ' selected' : ''}>${s}</option>`)
      .join('');

    const reviewerOptions = (all_employees || [])
      .filter(e => e.id !== card.emp_id)
      .map(e => `<option value="${esc(e.name)}"${card.reviewer_name === e.name ? ' selected' : ''}>${esc(e.name)}</option>`)
      .join('');

    const roleByName = Object.fromEntries((all_employees || []).map(e => [e.name, e.role]));
    const timelineHtml = _buildTimeline(activities || [], comments || [], roleByName);

    // Update panel title + crumb
    const titleEl  = document.getElementById('card-panel-title') || document.getElementById('card-modal-title');
    const crumbEl  = document.getElementById('card-panel-crumb');
    if (titleEl) titleEl.textContent = card.task_title;
    if (crumbEl) crumbEl.textContent = `Production Board  ›  ${card.status}`;

    const uc = {'Low':'#64748b','Normal':'#94a3b8','High':'#f59e0b','Critical':'#ef4444'};
    const urgColor = uc[card.urgency || 'Normal'] || '#94a3b8';
    const clientInfo = (clients || []).find(c => c.name === card.client);
    const hoursWorked = card.hours_worked || 0;
    const overEstimate = card.estimated_hours && hoursWorked > card.estimated_hours;
    const clientOptions = [`<option value="">— No client —</option>`]
      .concat((clients || []).map(c => `<option value="${esc(c.name)}"${c.name === card.client ? ' selected' : ''}>${esc(c.name)}</option>`))
      .concat(card.client && !clientInfo ? [`<option value="${esc(card.client)}" selected>${esc(card.client)} (inactive)</option>`] : [])
      .join('');

    body.innerHTML = `
      <style>
        .cp-prop { display:flex; flex-direction:column; gap:3px; }
        .cp-prop-label { font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
        .cp-prop-val   { font-size:13px; font-weight:500; color:var(--text); }
        .cp-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px 20px; margin-bottom:16px; }
        .cp-divider { border:none; border-top:1px solid var(--border); margin:14px 0; }
        .activity-event {
          display:flex; align-items:flex-start; gap:8px;
          padding:6px 0; font-size:.8rem; color:var(--muted);
          border-left:2px solid var(--border); margin-left:8px; padding-left:12px;
        }
        .activity-icon { flex-shrink:0; }
        .activity-body { flex:1; color:var(--text-2); }
        .activity-body strong { color:var(--text); font-weight:600; }
        .activity-time { white-space:nowrap; margin-left:auto; flex-shrink:0; font-size:.72rem; }
        .comment { background:var(--surface2,#f8fafc); border-radius:8px; padding:9px 12px; margin:4px 0; }
        .comment-author { font-weight:700; font-size:.8rem; }
        .comment-text { font-size:.82rem; margin-top:3px; line-height:1.5; white-space:pre-wrap; }
        .timeline-wrap { max-height:320px; overflow-y:auto; padding-right:4px; }
      </style>

      <!-- Status strip -->
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap;">
        <span class="badge badge-${_statusColor[card.status] || 'gray'}">${esc(card.status)}</span>
        ${card.urgency && card.urgency !== 'Normal' ? `<span style="font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:99px;background:${urgColor}22;color:${urgColor};">${esc(card.urgency)}</span>` : ''}
        ${card.client ? _clientChip(card.client, clientInfo) : ''}
        ${card.is_archived ? '<span class="badge badge-gray">🗄️ Archived</span>' : ''}
      </div>

      <!-- Properties grid -->
      <div class="cp-grid">
        <div class="cp-prop">
          <span class="cp-prop-label">Assignee</span>
          <span class="cp-prop-val fw-600">${esc(card.emp_name)}${roleTag(card.emp_role)}</span>
          ${card.created_by_name ? `<span style="font-size:.72rem;color:var(--muted);">by ${esc(card.created_by_name)}</span>` : ''}
        </div>
        <div class="cp-prop">
          <span class="cp-prop-label">Hours Worked</span>
          <span class="cp-prop-val fw-600" ${overEstimate ? 'style="color:#d97706;" title="Over the estimate"' : ''}>
            ${hoursWorked}h${card.estimated_hours ? ` <span style="font-weight:500;color:var(--muted);">/ ${card.estimated_hours}h est.</span>` : ''}
          </span>
          ${card.review_hours ? `<span style="font-size:.72rem;color:var(--muted);">+ ${card.review_hours}h review</span>` : ''}
        </div>
        <div class="cp-prop">
          <span class="cp-prop-label">Estimated Time</span>
          ${can_edit ? `<div style="display:flex;align-items:center;gap:6px;">
            <input type="number" id="estimate-input" min="0.25" max="200" step="0.25" value="${card.estimated_hours || ''}"
                   placeholder="hours" class="form-input" style="width:90px;height:26px;font-size:.78rem;padding:2px 6px;">
            <button class="btn btn-outline btn-sm" style="height:26px;font-size:.72rem;padding:0 8px;" onclick="saveEstimate(${card.id})">Save</button>
          </div>` : `<span class="cp-prop-val">${card.estimated_hours ? card.estimated_hours + 'h' : '—'}</span>`}
        </div>
        <div class="cp-prop">
          <span class="cp-prop-label">Client</span>
          ${can_edit ? `<div style="display:flex;align-items:center;gap:6px;">
            <select id="client-select" class="form-select" style="height:28px;font-size:.78rem;padding:2px 6px;max-width:160px;">${clientOptions}</select>
            <button class="btn btn-outline btn-sm" style="height:26px;font-size:.72rem;padding:0 8px;" onclick="saveClient(${card.id})">Save</button>
          </div>` : `<span class="cp-prop-val">${card.client ? _clientChip(card.client, clientInfo) : '—'}</span>`}
        </div>
        <div class="cp-prop">
          <span class="cp-prop-label">Due Date</span>
          <div style="display:flex;align-items:center;gap:6px;">
            <input type="date" id="due-date-input" value="${card.due_date || ''}"
                   class="form-input" style="width:140px;height:26px;font-size:.78rem;padding:2px 6px;">
            <button class="btn btn-outline btn-sm" style="height:26px;font-size:.72rem;padding:0 8px;" onclick="saveDueDate(${card.id})">Save</button>
          </div>
        </div>
        <div class="cp-prop">
          <span class="cp-prop-label">Date Logged</span>
          <span class="cp-prop-val">${esc(card.date_logged || '—')}</span>
        </div>
      </div>

      <div class="mb-12">
        <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">RE-ASSIGN TO</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <select id="assign-emp-select" class="form-select" style="width:auto;font-size:.82rem;">
            <option value="">— Keep current —</option>
            ${(all_employees||[]).map(e=>`<option value="${e.id}"${e.id===card.emp_id?' selected':''}>${esc(e.name)}</option>`).join('')}
          </select>
          <button class="btn btn-outline btn-sm" onclick="saveAssignee(${card.id})">Assign</button>
        </div>
      </div>

      ${can_edit ? `
      <div class="mb-12">
        <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">NOTES</div>
        <div class="rte-wrap">
          <div class="rte-toolbar">
            ${_RTE_BUTTONS.map(b => `<button type="button" title="${b.title}" onmousedown="event.preventDefault();cardRte('${b.cmd}')">${b.label}</button>`).join('')}
          </div>
          <div id="card-notes-rte" class="text-sm kn-notes-body rte-body" contenteditable="true"
               data-placeholder="Add notes, steps or context…">${card.notes || ''}</div>
        </div>
        <div style="display:flex;justify-content:flex-end;margin-top:6px;">
          <button class="btn btn-outline btn-sm" onclick="saveNotes(${card.id})">Save Notes</button>
        </div>
      </div>` : card.notes ? `
      <div class="mb-12">
        <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">NOTES</div>
        <div class="text-sm kn-notes-body" style="padding:10px;border-radius:6px;line-height:1.65;">${card.notes}</div>
      </div>` : ''}

      <!-- ── Drive Files ─────────────────────────────────────────────── -->
      <div class="mb-12">
        <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em; display:flex; align-items:center; justify-content:space-between;">
          <span>📥 INPUT FILES</span>
          ${canReview ? `<label class="btn btn-outline btn-sm" for="input-file-pick-${card.id}" style="cursor:pointer;font-size:.7rem;padding:2px 8px;margin:0;">+ Upload</label>
          <input type="file" id="input-file-pick-${card.id}" style="display:none;" onchange="uploadTaskFile(${card.id},'input',this)">` : ''}
        </div>
        <div id="input-files-list-${card.id}">
          ${(input_files||[]).length === 0 ? '<span class="text-xs text-muted">No input files yet.</span>' :
            (input_files||[]).map(f => _driveFileCard(f, canReview)).join('')}
        </div>
      </div>

      <div class="mb-12">
        <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em; display:flex; align-items:center; justify-content:space-between;">
          <span>📤 OUTPUT FILES</span>
          ${(isOwnCard || canReview) ? `<label class="btn btn-outline btn-sm" for="output-file-pick-${card.id}" style="cursor:pointer;font-size:.7rem;padding:2px 8px;margin:0;">+ Upload</label>
          <input type="file" id="output-file-pick-${card.id}" style="display:none;" onchange="uploadTaskFile(${card.id},'output',this)">` : ''}
        </div>
        <div id="output-files-list-${card.id}">
          ${(output_files||[]).length === 0 ? '<span class="text-xs text-muted">No output files yet.</span>' :
            (output_files||[]).map(f => _driveFileCard(f, canReview)).join('')}
        </div>
      </div>

      <div class="flex gap-6 mb-12 flex-wrap">
        ${card.hr_reviewed_by   ? `<span class="badge badge-green">✓ Reviewed: ${esc(card.hr_reviewed_by)}</span>` : ''}
        ${card.admin_approved_by? `<span class="badge badge-blue">🔒 Approved: ${esc(card.admin_approved_by)}</span>` : ''}
      </div>

      <div class="section-divider">Assign Reviewer</div>
      <div class="flex gap-8 mb-16 flex-wrap" style="align-items:flex-end;">
        <div style="flex:1;min-width:160px;">
          <select id="reviewer-select" class="form-select" style="font-size:.82rem;">
            <option value="">— No reviewer —</option>
            ${reviewerOptions}
          </select>
        </div>
        <button class="btn btn-outline btn-sm" onclick="saveReviewer(${card.id})">Save</button>
        ${card.reviewer_name ? `<span class="badge badge-blue">👤 ${esc(card.reviewer_name)}</span>` : ''}
      </div>

      ${canMove ? `
      <div class="section-divider">Move Card</div>
      <form method="post" action="/api/tasks/${card.id}/move-form" class="flex gap-8 mb-16 flex-wrap" style="align-items:center;"
            data-own="${isOwnCard}" data-person="${esc(card.emp_name)}" data-est="${card.estimated_hours || ''}">
        <select name="status" class="form-select" style="width:auto;font-size:.82rem;">${statusOptions}</select>
        <button class="btn btn-primary btn-sm" type="submit">Move</button>
      </form>` : ''}

      ${(canReview || canPeerReview) && card.status === 'For Review' ? `
      <div class="flex gap-8 flex-wrap mb-16" style="border-top:1px solid var(--border);padding-top:14px;">
        <form method="post" action="/api/tasks/${card.id}/review" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
          <input type="hidden" name="_csrf" value="${document.querySelector('meta[name=csrf-token]')?.content || ''}">
          <input type="number" name="review_hours" required min="0.25" max="200" step="0.25" placeholder="Review hrs"
                 title="How many hours did you spend reviewing?" class="form-input" style="width:100px;height:30px;font-size:.8rem;">
          <button class="btn btn-success btn-sm">✓ ${canReview ? 'Mark Reviewed → Done' : 'Peer Review → Done'}</button>
        </form>
        <form method="post" action="/api/tasks/${card.id}/return" id="return-form-${card.id}" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
          <input type="hidden" name="_csrf" value="${document.querySelector('meta[name=csrf-token]')?.content || ''}">
          <input type="text" name="return_note" placeholder="Return note…" class="form-input" style="width:180px;height:30px;font-size:.8rem;">
          <button class="btn btn-warning btn-sm" type="submit">↩ Return for Revision</button>
        </form>
        ${card.revision_count > 0 ? `<span class="badge badge-yellow">Rev. #${card.revision_count}</span>` : ''}
        ${canApprove ? `<form method="post" action="/api/tasks/${card.id}/approve">
          <input type="hidden" name="_csrf" value="${document.querySelector('meta[name=csrf-token]')?.content || ''}">
          <button class="btn btn-primary btn-sm">🔒 Admin Approve</button>
        </form>` : ''}
      </div>` : ''}

      ${canArchive ? `
      <div class="mb-16">
        <button class="btn btn-outline btn-sm" style="color:var(--muted);"
                onclick="toggleArchive(${card.id}, ${card.is_archived ? 1 : 0})">
          ${card.is_archived ? '📤 Unarchive' : '🗄️ Archive Card'}
        </button>
      </div>` : ''}

      <div class="section-divider">Activity</div>
      <div class="timeline-wrap" id="timeline-list">${timelineHtml}</div>

      <form id="comment-form" class="flex gap-8 mt-12" style="align-items:flex-start;">
        <textarea id="comment-input" class="form-textarea" rows="2"
                  placeholder="Add a comment… (type @ to mention someone)"
                  style="flex:1;min-height:60px;resize:vertical;"></textarea>
        <button type="submit" class="btn btn-primary btn-sm" style="white-space:nowrap;margin-top:2px;">Post</button>
      </form>
    `;

    const commentForm = document.getElementById('comment-form');
    attachMentionAutocomplete(document.getElementById('comment-input'),
                              (all_employees || []).filter(e => e.id !== user_id).map(e => e.name));
    if (commentForm) {
      commentForm.addEventListener('submit', async e => {
        e.preventDefault();
        const input = document.getElementById('comment-input');
        const text  = input.value.trim();
        if (!text) return;
        const btn = commentForm.querySelector('button[type="submit"]');
        btn.disabled = true;
        const r = await apiFetch(`/api/tasks/${taskId}/comment`, {
          method: 'POST',
          body: new URLSearchParams({ comment_text: text }),
        });
        btn.disabled = false;
        if (r.ok) {
          const { comment } = await r.json();
          input.value = '';
          const tl = document.getElementById('timeline-list');
          if (tl) {
            tl.insertAdjacentHTML('afterbegin', `
              <div class="comment">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                  <span class="comment-author">${esc(comment.author_name)}${roleTag(user_role)}</span>
                  <span class="comment-time text-xs text-muted">${esc(comment.timestamp)}</span>
                </div>
                <div class="comment-text">${highlightMentions(esc(comment.comment_text), Object.keys(roleByName)).replace(/\n/g,'<br>')}</div>
              </div>`);
            tl.scrollTop = 0;
          }
        }
      });
    }

  } catch (err) {
    body.innerHTML = `<p class="text-danger" style="padding:16px">Failed to load card: ${err.message}</p>`;
  }
}

function _clientChip(name, info) {
  const color = (info && info.hex_color) || '#64748b';
  const logo = info && info.logo_url
    ? `<img src="${esc(info.logo_url)}" alt="" style="width:16px;height:16px;object-fit:contain;border-radius:3px;background:#fff;">`
    : `<span style="width:8px;height:8px;border-radius:50%;background:${esc(color)};display:inline-block;"></span>`;
  return `<span style="display:inline-flex;align-items:center;gap:5px;font-size:.74rem;font-weight:700;color:${esc(color)};">${logo}${esc(name)}</span>`;
}

const _RTE_BUTTONS = [
  { cmd: 'bold', label: '<b>B</b>', title: 'Bold' },
  { cmd: 'italic', label: '<i>I</i>', title: 'Italic' },
  { cmd: 'underline', label: '<u>U</u>', title: 'Underline' },
  { cmd: 'strikeThrough', label: '<s>S</s>', title: 'Strikethrough' },
  { cmd: 'insertUnorderedList', label: '•', title: 'Bullet list' },
  { cmd: 'insertOrderedList', label: '1.', title: 'Numbered list' },
  { cmd: 'removeFormat', label: '⌫', title: 'Clear formatting' },
];
function cardRte(cmd) {
  document.execCommand(cmd, false, null);
  document.getElementById('card-notes-rte')?.focus();
}

async function _postJson(url, body, okMsg) {
  const res = await apiFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.ok) showToast(okMsg, 'success');
  else showToast(res.status === 403 ? 'Not allowed.' : 'Could not save — please try again.', 'error');
  return res;
}

async function saveNotes(taskId) {
  const rte = document.getElementById('card-notes-rte');
  if (!rte) return;
  const res = await _postJson(`/api/tasks/${taskId}/set-notes`, { notes: rte.innerHTML.trim() }, 'Notes saved.');
  if (res.ok) { const data = await res.json(); rte.innerHTML = data.notes; }
}

async function saveClient(taskId) {
  const sel = document.getElementById('client-select');
  if (!sel) return;
  const res = await _postJson(`/api/tasks/${taskId}/set-client`, { client: sel.value }, 'Client updated.');
  if (res.ok) setTimeout(() => location.reload(), 700);
}

async function saveEstimate(taskId) {
  const input = document.getElementById('estimate-input');
  if (!input) return;
  const res = await _postJson(`/api/tasks/${taskId}/set-estimate`, { estimated_hours: input.value }, 'Estimate saved.');
  if (res.ok) setTimeout(() => location.reload(), 700);
}

async function toggleArchive(taskId, currentlyArchived) {
  const res = await apiFetch(`/api/tasks/${taskId}/archive`, { method: 'POST' });
  if (res.ok) {
    const { archived } = await res.json();
    showToast(archived ? 'Card archived.' : 'Card unarchived.', 'success');
    _closeActiveCardView();
    location.reload();
  } else {
    showToast('Could not archive card.', 'error');
  }
}

async function saveReviewer(taskId) {
  const sel = document.getElementById('reviewer-select');
  if (!sel) return;
  const name = sel.value;
  const form = new FormData();
  form.append('reviewer_name', name);
  const res = await apiFetch(`/api/tasks/${taskId}/assign-reviewer`, { method: 'POST', body: form });
  if (res.ok) {
    showToast(name ? `Reviewer assigned: ${name}` : 'Reviewer cleared', 'success');
  }
}

async function selfAssign(taskId) {
  const res = await apiFetch(`/api/tasks/${taskId}/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (res.ok) location.reload();
}

async function saveDueDate(taskId) {
  const input = document.getElementById('due-date-input');
  if (!input) return;
  const res = await apiFetch(`/api/tasks/${taskId}/set-due-date`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ due_date: input.value }),
  });
  if (res.ok) {
    showToast('Due date saved.', 'success');
    setTimeout(() => location.reload(), 800);
  } else {
    showToast('Could not save due date.', 'error');
  }
}

async function saveAssignee(taskId) {
  const sel = document.getElementById('assign-emp-select');
  if (!sel || !sel.value) return;
  const res = await apiFetch(`/api/tasks/${taskId}/transfer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ emp_id: parseInt(sel.value) }),
  });
  if (res.ok) {
    showToast('Task reassigned.', 'success');
    setTimeout(() => location.reload(), 800);
  } else {
    showToast('Could not reassign.', 'error');
  }
}

// Role emblems — mirrors vip_class()/role_tag() in app.py
const _ROLE_EMBLEMS = {
  'Admin':        ['vip vip-admin', 'role-tag role-tag-admin', '★ Admin'],
  'HR Manager':   ['vip vip-hr',    'role-tag role-tag-hr',    'HR'],
  'Team Manager': ['vip vip-lead',  'role-tag role-tag-lead',  'Lead'],
};
function vipClass(role) { return (_ROLE_EMBLEMS[role] || [''])[0]; }
function roleTag(role) {
  const e = _ROLE_EMBLEMS[role];
  return e ? `<span class="${e[1]}" title="${esc(role)}">${e[2]}</span>` : '';
}

// ── @mentions (task comments; chat has its own) ──────────────────────────────
function highlightMentions(html, names) {
  if (!names || !names.length) return html;
  const sorted = [...names].sort((a, b) => b.length - a.length).concat(MENTION_GROUPS)
    .map(n => esc(n).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp('@(' + sorted.join('|') + ')(?![\\w])', 'gi');
  return html.replace(re, '<span class="mention">@$1</span>');
}

const MENTION_GROUPS = ['everyone', 'teamlead', 'admin', 'hr', 'morningshift', 'nightshift'];

// names: array, or a function returning the current list.
// opts.groups: include @groups (boolean, or a function checked on each keystroke).
function attachMentionAutocomplete(input, names, opts = {}) {
  if (!input || input._mentionReady) return;
  input._mentionReady = true;
  const peopleNow = () => (typeof names === 'function' ? names() : names) || [];
  const groupsOn = () => typeof opts.groups === 'function' ? opts.groups() : opts.groups !== false;
  const drop = document.createElement('div');
  drop.className = 'mention-drop';
  input.parentNode.style.position = 'relative';
  input.parentNode.appendChild(drop);
  let start = -1, sel = -1;
  const close = () => { drop.classList.remove('open'); sel = -1; };
  const choose = name => {
    const pos = input.selectionStart;
    input.value = input.value.slice(0, start) + '@' + name + ' ' + input.value.slice(pos);
    input.selectionStart = input.selectionEnd = start + name.length + 2;
    input.focus(); close();
  };
  input.addEventListener('input', () => {
    const before = input.value.slice(0, input.selectionStart);
    const m = before.match(/(?:^|\s)@([\w.]*(?: [\w.]*)?)$/);
    if (!m) return close();
    const q = m[1].toLowerCase();
    start = input.selectionStart - m[1].length - 1;
    const pool = (groupsOn() ? MENTION_GROUPS : []).concat(peopleNow());
    const matches = pool.filter(n => n.toLowerCase().includes(q)).slice(0, 8);
    if (!matches.length) return close();
    drop.innerHTML = matches.map(n =>
      `<div class="mention-opt" data-name="${esc(n)}">${MENTION_GROUPS.includes(n) ? '📢' : '👤'} ${esc(n)}</div>`).join('');
    drop.querySelectorAll('.mention-opt').forEach(o =>
      o.addEventListener('mousedown', e => { e.preventDefault(); choose(o.dataset.name); }));
    drop.classList.add('open'); sel = -1;
  });
  input.addEventListener('keydown', e => {
    if (!drop.classList.contains('open')) return;
    const items = drop.querySelectorAll('.mention-opt');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items.forEach((o, i) => o.classList.toggle('sel', i === sel));
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      // Enter picks the highlighted (or first) suggestion instead of sending
      e.preventDefault(); e.stopImmediatePropagation(); choose(items[sel >= 0 ? sel : 0].dataset.name);
    } else if (e.key === 'Escape') { e.stopPropagation(); close(); }
  });
  input.addEventListener('blur', () => setTimeout(close, 150));
}

function esc(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Move card via inline form in modal ────────────────────────────────────────
document.addEventListener('submit', async e => {
  const form = e.target.closest('form[action$="/move-form"]');
  if (!form) return;
  e.preventDefault();
  const taskId = form.action.match(/\/api\/tasks\/(\d+)\/move-form/)[1];
  const status = form.querySelector('select[name="status"]').value;
  moveCard(taskId, status, { own: form.dataset.own === 'true', person: form.dataset.person, estimate: form.dataset.est });
});

// ── Confirm dangerous actions ─────────────────────────────────────────────────
document.querySelectorAll('[data-confirm]').forEach(el => {
  el.addEventListener('click', e => {
    if (!confirm(el.dataset.confirm)) e.preventDefault();
  });
});

// ── Toast notifications ───────────────────────────────────────────────────────
function showToast(msg, kind = 'success') {
  const t = document.createElement('div');
  t.className = `flash ${kind}`;
  t.style.cssText = 'position:fixed; bottom:24px; right:24px; z-index:9999; max-width:320px; animation:fadeIn .2s ease;';
  t.innerHTML = (kind === 'success' ? '✓ ' : '✗ ') + msg;
  document.body.appendChild(t);
  setTimeout(() => t.style.opacity = '0', 2500);
  setTimeout(() => t.remove(), 3000);
}

// ── Live shift timers (topbar pill + dashboard) ──────────────────────────────
// Elements carry data-shift-start (ISO with +08:00), data-break-sec (finished breaks)
// and data-break-start when on break — worked time then freezes at the break start.
function _fmtHMS(sec) {
  sec = Math.max(0, Math.floor(sec));
  return [Math.floor(sec / 3600), Math.floor((sec % 3600) / 60), sec % 60]
    .map(n => String(n).padStart(2, '0')).join(':');
}
function tickShiftTimers() {
  document.querySelectorAll('[data-shift-start]').forEach(el => {
    const start = Date.parse(el.dataset.shiftStart);
    if (isNaN(start)) return;
    const breakStart = el.dataset.breakStart ? Date.parse(el.dataset.breakStart) : NaN;
    const end = isNaN(breakStart) ? Date.now() : breakStart;
    el.textContent = _fmtHMS((end - start) / 1000 - (parseInt(el.dataset.breakSec) || 0));
  });
  document.querySelectorAll('[data-break-since]').forEach(el => {
    const since = Date.parse(el.dataset.breakSince);
    if (!isNaN(since)) el.textContent = _fmtHMS((Date.now() - since) / 1000);
  });
}
tickShiftTimers();
setInterval(tickShiftTimers, 1000);

// ── Shared "add task" form (templates/shared/_task_form.html) ─────────────────
function taskFormRte(cmd) {
  document.execCommand(cmd, false, null);
  document.getElementById('kb-notes-rte')?.focus();
}
function initTaskForm() {
  const form = document.getElementById('add-task-form');
  if (!form) return;
  const filePick   = document.getElementById('at-input-file-pick');
  const fileLabel  = document.getElementById('at-file-label');
  const fileStatus = document.getElementById('at-file-status');
  const submitBtn  = document.getElementById('at-submit-btn');
  filePick?.addEventListener('change', async () => {
    const file = filePick.files[0];
    if (!file) return;
    fileLabel.textContent = '⏫ Uploading…';
    fileStatus.textContent = '';
    submitBtn.disabled = true;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res  = await apiFetch('/api/chat/upload', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.ok) {
        document.getElementById('at-input-drive-id').value  = data.drive_id;
        document.getElementById('at-input-file-name').value = data.name;
        document.getElementById('at-input-file-size').value = file.size;
        fileLabel.textContent  = '✓ ' + data.name;
        fileStatus.textContent = file.size > 1048576 ? (file.size / 1048576).toFixed(1) + ' MB' : Math.round(file.size / 1024) + ' KB';
      } else {
        fileLabel.textContent = '📎 Choose File';
        fileStatus.textContent = '✕ Upload failed';
      }
    } catch {
      fileLabel.textContent = '📎 Choose File';
      fileStatus.textContent = '✕ Error';
    }
    submitBtn.disabled = false;
  });
  form.addEventListener('submit', () => {
    const rte = document.getElementById('kb-notes-rte');
    if (rte) document.getElementById('kb-notes-val').value = rte.innerHTML.trim();
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving…';
  });
}
document.addEventListener('DOMContentLoaded', initTaskForm);

// ── Office PC chip (helper on this computer → signed snapshot → HF Hub) ───────
const HF_HELPER_URL = 'http://127.0.0.1:47600/snapshot';
const _PC_LABELS = { ok: 'Good', warn: 'Warning', bad: 'Problem', none: 'Not on an office PC' };
function _pcSeenHelper(set) {
  try { if (set) localStorage.setItem('hf_pc_helper', '1'); return localStorage.getItem('hf_pc_helper') === '1'; }
  catch { return false; }
}
function _pcShouldProbe() {
  const wrap = document.getElementById('pc-wrap');
  // Only from the office network (or a browser that has met the helper) — no Chrome prompt at home
  return !!wrap && (wrap.dataset.probe === '1' || _pcSeenHelper() || location.pathname === '/office-pcs');
}
async function getPcSnapshot(timeoutMs = 2500) {
  if (!_pcShouldProbe()) return null;
  try {
    const r = await fetch(HF_HELPER_URL, { cache: 'no-store', signal: AbortSignal.timeout(timeoutMs) });
    if (!r.ok) return null;
    const snap = await r.json();
    _pcSeenHelper(true);
    return snap;
  } catch { return null; }
}
function _pcNet(pc) {
  if (pc.link_type === 'wifi') return 'Wi-Fi';
  if (pc.link_type === 'cable') return pc.link_mbps >= 1000 ? (pc.link_mbps / 1000) + ' Gbps cable' : (pc.link_mbps || '?') + ' Mbps cable';
  return 'Not connected';
}
function renderPcChip(pc) {
  const chip = document.getElementById('pc-chip'), pop = document.getElementById('pc-pop');
  if (!chip || !pop) return;
  const state = pc ? pc.status : 'none';
  chip.className = 'pc-chip pcicon--' + state;
  chip.querySelector('.pcicon').className = 'pcicon pcicon--' + state;
  document.getElementById('pc-chip-label').textContent = pc ? pc.name : 'No office PC';
  chip.title = pc ? `${pc.name} — ${_PC_LABELS[state]}` : 'Not on an office PC';
  if (!pc) {
    pop.innerHTML = `<div class="pc-pop-head"><span class="pc-pop-title">${chip.querySelector('.pcicon').outerHTML}
        <span><strong>Not on an office PC</strong><br><span class="text-xs text-muted">Phone, home computer, or helper not installed</span></span></span></div>
      <div class="text-sm text-muted">No office computer details are saved with your Time In from this device.</div>`;
    return;
  }
  const netState = (pc.warnings || []).some(w => /Wi-Fi|Mbps|network/i.test(w.message)) ? 'st-warn' : '';
  const disks = (pc.disks || []).map(d => {
    const used = typeof d.used_pct === 'number' ? Math.round(d.used_pct) + '% used' : 'usage n/a';
    const health = d.health ? ' · ' + esc(d.health) : '';
    const wear = typeof d.wear_pct === 'number' ? ' · ' + Math.round(d.wear_pct) + '% worn' : '';
    const cls = d.state === 'bad' ? 'st-bad' : d.state === 'warn' ? 'st-warn' : '';
    return `<div class="pc-row"><span class="k">SSD ${esc(d.name)}</span><span class="v ${cls}">${used}${health}${wear}</span></div>`;
  }).join('');
  const warns = (pc.warnings || []).map(w =>
    `<div class="pc-warn pcicon--${w.severity}"><b>⚠</b><span>${esc(w.message)}</span></div>`).join('');
  pop.innerHTML = `
    <div class="pc-pop-head">
      <span class="pc-pop-title">${chip.querySelector('.pcicon').outerHTML}
        <span><strong>${esc(pc.name)}</strong><br><span class="text-xs text-muted">${pc.platform === 'mac' ? 'Mac' : 'Windows PC'}</span></span></span>
      <span class="pc-pill pc-pill--${state}">${_PC_LABELS[state]}</span>
    </div>
    <div class="pc-row"><span class="k">IP address</span><span class="v">${esc(pc.ip || '—')}</span></div>
    <div class="pc-row"><span class="k">Network</span><span class="v ${netState}">${_pcNet(pc)}${pc.expected_link === 'wifi' && pc.link_type === 'wifi' ? ' (normal for this computer)' : ''}</span></div>
    ${disks}${warns}
    <div class="pc-foot">Checked ${esc((pc.taken_at || '').slice(11, 16))} · updates each time you open HF Hub</div>`;
}
async function refreshPcChip() {
  if (!document.getElementById('pc-wrap')) return;
  const snap = await getPcSnapshot();
  if (!snap) { renderPcChip(null); return; }
  try {
    const res = await apiFetch('/api/pc/snapshot', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(snap),
    });
    const data = await res.json();
    renderPcChip(data.ok ? data.pc : null);
  } catch { renderPcChip(null); }
}
document.addEventListener('click', e => {
  const chip = document.getElementById('pc-chip'), pop = document.getElementById('pc-pop');
  if (!chip || !pop) return;
  if (chip.contains(e.target)) {
    const open = pop.classList.toggle('open');
    chip.setAttribute('aria-expanded', open);
  } else if (!pop.contains(e.target)) {
    pop.classList.remove('open'); chip.setAttribute('aria-expanded', 'false');
  }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('pc-pop')?.classList.remove('open'); });
// Time In / Time Out: attach the signed snapshot to the form so it's saved with the attendance record
document.addEventListener('submit', async e => {
  const form = e.target.closest('form[action$="/api/clock-in"], form[action$="/api/clock-out"]');
  if (!form || form.dataset.pcAttached) return;
  e.preventDefault();
  const btn = form.querySelector('button[type="submit"]');
  if (btn) btn.disabled = true;
  const snap = await getPcSnapshot(2000);
  if (snap) {
    for (const [name, value] of [['pc_payload', snap.payload], ['pc_sig', snap.sig]]) {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = name; input.value = value;
      form.appendChild(input);
    }
  }
  form.dataset.pcAttached = '1';
  form.submit();
});
document.addEventListener('DOMContentLoaded', refreshPcChip);

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', initKanban);
