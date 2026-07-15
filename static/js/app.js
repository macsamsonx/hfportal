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
    const canDrag = role === 'HR Manager' || role === 'Admin' || isOwn;

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

      try {
        const res = await apiFetch(`/api/tasks/${taskId}/move`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: newStatus }),
        });
        if (res.ok) {
          location.reload();
        } else {
          showToast('Could not move card — please try again.', 'error');
        }
      } catch {
        showToast('Network error — please refresh.', 'error');
      }
    });
  });
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
};
const _statusColor = { 'Todo': 'gray', 'In Progress': 'blue', 'For Review': 'yellow', 'Done': 'green' };

function _buildTimeline(activities, comments) {
  const items = [
    ...activities.map(a => ({ ...a, _kind: 'event',   _ts: a.created_at })),
    ...comments.map(c  => ({ ...c, _kind: 'comment', _ts: c.timestamp  })),
  ].sort((a, b) => a._ts.localeCompare(b._ts));

  if (!items.length) return '<p class="text-muted text-sm" style="padding:8px 0;">No activity yet.</p>';

  return items.map(item => {
    if (item._kind === 'comment') {
      return `
        <div class="comment">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
            <span class="comment-author">${esc(item.author_name)}</span>
            <span class="comment-time text-xs text-muted">${esc(item._ts)}</span>
          </div>
          <div class="comment-text">${esc(item.comment_text).replace(/\n/g,'<br>')}</div>
        </div>`;
    }
    const icon = _activityIcon[item.activity_type] || '•';
    return `
      <div class="activity-event">
        <span class="activity-icon">${icon}</span>
        <span class="activity-body">
          <strong>${esc(item.actor_name)}</strong>
          ${item.detail ? esc(item.detail) : item.activity_type.replace('_', ' ')}
        </span>
        <span class="activity-time text-xs text-muted">${esc(item._ts)}</span>
      </div>`;
  }).join('');
}

async function openCardModal(taskId) {
  const modal = document.getElementById('card-modal');
  const body  = document.getElementById('card-modal-body');
  if (!modal || !body) return;

  body.innerHTML = '<div class="empty-state"><div class="emoji">⏳</div><p>Loading…</p></div>';
  openModal('card-modal');

  try {
    const res = await fetch(`/api/tasks/${taskId}/detail`);
    if (!res.ok) { body.innerHTML = '<p class="text-danger" style="padding:16px">Error loading card.</p>'; return; }
    const { card, comments, activities, statuses, user_role, user_name, user_id, all_employees, input_files, output_files } = await res.json();

    const isOwnCard     = card.emp_id === user_id;
    const canReview     = user_role === 'HR Manager' || user_role === 'Admin';
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

    const timelineHtml = _buildTimeline(activities || [], comments || []);

    body.innerHTML = `
      <style>
        .activity-event {
          display:flex; align-items:flex-start; gap:8px;
          padding:5px 0; font-size:.8rem; color:var(--muted);
          border-left:2px solid var(--border); margin-left:8px; padding-left:12px;
        }
        .activity-icon { flex-shrink:0; font-size:.85rem; }
        .activity-body { flex:1; color:var(--text-2); }
        .activity-body strong { color:var(--text); font-weight:600; }
        .activity-time { white-space:nowrap; margin-left:auto; flex-shrink:0; }
        .comment { margin:4px 0; }
        .timeline-wrap { max-height:340px; overflow-y:auto; padding-right:4px; }
      </style>

      <div class="form-row mb-12">
        <div>
          <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">TASK</div>
          <div class="fw-700" style="font-size:.95rem;">${esc(card.task_title)}</div>
        </div>
        <div>
          <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">STATUS</div>
          <span class="badge badge-${_statusColor[card.status] || 'gray'}">${esc(card.status)}</span>
          ${card.is_archived ? '<span class="badge badge-gray" style="margin-left:4px;">🗄️ Archived</span>' : ''}
        </div>
      </div>

      <div class="form-row mb-12">
        <div>
          <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">ASSIGNED TO</div>
          <div class="text-sm fw-600">${esc(card.emp_name)}</div>
          ${card.created_by_name ? `<div class="text-xs text-muted" style="margin-top:2px;">Created by: ${esc(card.created_by_name)}</div>` : ''}
        </div>
        <div>
          <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">CLIENT</div>
          <div class="text-sm">${esc(card.client || '—')}</div>
        </div>
      </div>

      <div class="form-row mb-12">
        <div>
          <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">HOURS LOGGED</div>
          <div class="text-sm fw-600">${card.hours_worked}h</div>
        </div>
        <div>
          <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">URGENCY</div>
          ${(() => { const uc={'Low':'#64748b','Normal':'#94a3b8','High':'#f59e0b','Critical':'#ef4444'}; const u=card.urgency||'Normal'; return `<span style="font-size:.75rem;font-weight:700;color:${uc[u]||'#94a3b8'}">${u}</span>`; })()}
        </div>
      </div>

      <div class="form-row mb-12">
        <div>
          <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">DUE DATE</div>
          <div style="display:flex;align-items:center;gap:6px;">
            <input type="date" id="due-date-input" value="${card.due_date || ''}"
                   class="form-input" style="width:150px;height:28px;font-size:.8rem;padding:2px 8px;">
            <button class="btn btn-outline btn-sm" style="height:28px;font-size:.75rem;" onclick="saveDueDate(${card.id})">Save</button>
          </div>
        </div>
        <div>
          <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">DATE LOGGED</div>
          <div class="text-sm">${esc(card.date_logged || '—')}</div>
        </div>
      </div>

      ${canReview ? `
      <div class="mb-12">
        <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">RE-ASSIGN TO</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <select id="assign-emp-select" class="form-select" style="width:auto;font-size:.82rem;">
            <option value="">— Keep current —</option>
            ${(all_employees||[]).map(e=>`<option value="${e.id}"${e.id===card.emp_id?' selected':''}>${esc(e.name)}</option>`).join('')}
          </select>
          <button class="btn btn-outline btn-sm" onclick="saveAssignee(${card.id})">Assign</button>
        </div>
      </div>` : ''}

      ${card.notes ? `
      <div class="mb-12">
        <div class="text-xs text-muted fw-600 mb-4" style="letter-spacing:.06em;">NOTES</div>
        <div class="text-sm kn-notes-body" style="padding:10px;border-radius:6px;line-height:1.65;">${card.notes.includes('<') ? card.notes : esc(card.notes).replace(/\n/g,'<br>')}</div>
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
      <form method="post" action="/api/tasks/${card.id}/move-form" class="flex gap-8 mb-16 flex-wrap" style="align-items:center;">
        <select name="status" class="form-select" style="width:auto;font-size:.82rem;">${statusOptions}</select>
        <button class="btn btn-primary btn-sm" type="submit">Move</button>
      </form>` : ''}

      ${(canReview || canPeerReview) && card.status === 'For Review' ? `
      <div class="flex gap-8 flex-wrap mb-16" style="border-top:1px solid var(--border);padding-top:14px;">
        <form method="post" action="/api/tasks/${card.id}/review">
          <input type="hidden" name="_csrf" value="${document.querySelector('meta[name=csrf-token]')?.content || ''}">
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
                  placeholder="Add a comment…"
                  style="flex:1;min-height:60px;resize:vertical;"></textarea>
        <button type="submit" class="btn btn-primary btn-sm" style="white-space:nowrap;margin-top:2px;">Post</button>
      </form>
    `;

    document.getElementById('card-modal-title').textContent = card.task_title;

    const commentForm = document.getElementById('comment-form');
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
            tl.innerHTML += `
              <div class="comment">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                  <span class="comment-author">${esc(comment.author_name)}</span>
                  <span class="comment-time text-xs text-muted">${esc(comment.timestamp)}</span>
                </div>
                <div class="comment-text">${esc(comment.comment_text).replace(/\n/g,'<br>')}</div>
              </div>`;
            tl.scrollTop = tl.scrollHeight;
          }
        }
      });
    }

    // Scroll timeline to bottom
    const tl = document.getElementById('timeline-list');
    if (tl) tl.scrollTop = tl.scrollHeight;

  } catch (err) {
    body.innerHTML = `<p class="text-danger" style="padding:16px">Failed to load card: ${err.message}</p>`;
  }
}

async function toggleArchive(taskId, currentlyArchived) {
  const res = await apiFetch(`/api/tasks/${taskId}/archive`, { method: 'POST' });
  if (res.ok) {
    const { archived } = await res.json();
    showToast(archived ? 'Card archived.' : 'Card unarchived.', 'success');
    closeModal('card-modal');
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
  const res = await apiFetch(`/api/tasks/${taskId}/assign`, {
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
  const action = form.action.replace('/move-form', '/move');
  const status = form.querySelector('select[name="status"]').value;
  const res = await apiFetch(action, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  if (res.ok) location.reload();
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

// ── Attendance pill timer ─────────────────────────────────────────────────────
(function () {
  const timerEl = document.getElementById('att-timer');
  if (!timerEl) return;
  const ci = timerEl.dataset.ci; // "HH:MM" (PHT)
  if (!ci) return;
  const [h, m] = ci.split(':').map(Number);
  const now = new Date();
  const start = new Date(now);
  start.setHours(h, m, 0, 0);
  if (start > now) start.setDate(start.getDate() - 1);
  function tick() {
    const diff = Math.floor((Date.now() - start) / 1000);
    const hh = Math.floor(diff / 3600).toString().padStart(2, '0');
    const mm = Math.floor((diff % 3600) / 60).toString().padStart(2, '0');
    const ss = (diff % 60).toString().padStart(2, '0');
    timerEl.textContent = hh + ':' + mm + ':' + ss;
  }
  tick();
  setInterval(tick, 1000);
})();

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', initKanban);
