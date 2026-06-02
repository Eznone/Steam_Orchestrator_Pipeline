// ── State ──────────────────────────────────────────────────────────────────
let currentDem = null;
let parsedData  = null;
let clips       = [];

// ── Elements ───────────────────────────────────────────────────────────────
const fileSelect    = document.getElementById('file-select');
const customPath    = document.getElementById('custom-path');
const loadBtn       = document.getElementById('load-btn');
const playerSelect  = document.getElementById('player-select');
const analyzeBtn    = document.getElementById('analyze-btn');
const logEl         = document.getElementById('log');
const matchInfo     = document.getElementById('match-info');
const contentEl     = document.getElementById('content');
const emptyState    = document.getElementById('empty-state');
const captureBar    = document.getElementById('capture-bar');
const selCount      = document.getElementById('sel-count');
const captureBtn    = document.getElementById('capture-btn');
const deselectBtn   = document.getElementById('deselect-all-btn');

// ── Helpers ────────────────────────────────────────────────────────────────
function setLog(msg, type = '') {
  logEl.className = type;
  logEl.innerHTML = msg;
}

function ticksToTime(ticks, tickRate) {
  const totalSec = Math.floor(ticks / tickRate);
  const m = Math.floor(totalSec / 60);
  const s = String(totalSec % 60).padStart(2, '0');
  return `${m}:${s}`;
}

function clipBadgeClass(clip) {
  const r = clip.reason;
  if (r.startsWith('Double'))  return 'badge-double';
  if (r.startsWith('Triple'))  return 'badge-triple';
  if (r.startsWith('Quad'))    return 'badge-quad';
  if (r.startsWith('Penta'))   return 'badge-penta';
  if (clip.event_type === 'single_kill') return 'badge-single';
  if (clip.event_type === 'objective')   return 'badge-objective';
  return 'badge-streak';
}

function teamLabel(teamNum) {
  const labels = { 1: 'Spectator', 2: 'Amber Hand', 3: 'Sapphire Flame' };
  return labels[teamNum] || `Team ${teamNum}`;
}

function teamClass(teamNum) {
  return teamNum === 2 ? 'team-2' : 'team-3';
}

function winnerLabel(teamNum) {
  if (teamNum === null || teamNum === undefined) return 'N/A';
  return `<span class="team-badge ${teamClass(teamNum)}">${teamLabel(teamNum)}</span>`;
}

// ── File list ──────────────────────────────────────────────────────────────
async function loadFileList() {
  try {
    const res  = await fetch('/api/files');
    const data = await res.json();
    data.files.forEach(f => {
      const opt  = document.createElement('option');
      opt.value  = f.path;
      opt.text   = f.name;
      fileSelect.appendChild(opt);
    });
  } catch { /* silently ignore if no files */ }
}

// ── Load match ─────────────────────────────────────────────────────────────
loadBtn.addEventListener('click', async () => {
  const path = customPath.value.trim() || fileSelect.value;
  if (!path) { setLog('Select or enter a .dem file path.', 'error'); return; }

  setLog('<span class="spinner"></span>Parsing demo…');
  loadBtn.disabled = true;

  try {
    const res  = await fetch('/api/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dem_path: path }),
    });
    const data = await res.json();

    if (data.status !== 'ok') {
      setLog(`Error: ${data.message}`, 'error');
      return;
    }

    currentDem = path;
    parsedData = data.data;

    document.getElementById('info-id').textContent     = parsedData.match_id;
    document.getElementById('info-dur').textContent    = parsedData.total_clock_time;
    document.getElementById('info-map').textContent    = parsedData.map_name;
    document.getElementById('info-winner').innerHTML   = winnerLabel(parsedData.winning_team_num);
    document.getElementById('info-kills').textContent  = parsedData.kills.length;
    matchInfo.classList.add('visible');

    playerSelect.innerHTML = '<option value="">All players</option>';
    parsedData.players
      .sort((a, b) => a.team_num - b.team_num || a.player_name.localeCompare(b.player_name))
      .forEach(p => {
        const opt   = document.createElement('option');
        opt.value   = p.steam_id;
        opt.text    = `${p.player_name} (${p.hero_name}) · ${teamLabel(p.team_num)}`;
        playerSelect.appendChild(opt);
      });

    document.getElementById('player-section').style.display  = '';
    document.getElementById('event-section').style.display   = '';
    document.getElementById('timing-section').style.display  = '';

    setLog(`Loaded match ${parsedData.match_id} — ${parsedData.players.length} players, ${parsedData.kills.length} kills.`, 'ok');
    clearClips();

  } catch (err) {
    setLog(`Unexpected error: ${err.message}`, 'error');
  } finally {
    loadBtn.disabled = false;
  }
});

// ── Event type param visibility ─────────────────────────────────────────────
const eventTypeSelect = document.getElementById('event-type');
const allParamDivs = ['multikill', 'kill_streak', 'objective'];

function updateParamVisibility() {
  const val = eventTypeSelect.value;
  allParamDivs.forEach(id => {
    document.getElementById(`params-${id}`).style.display = (id === val) ? '' : 'none';
  });
  const playerSection = document.getElementById('player-section');
  if (playerSection) playerSection.style.opacity = val === 'objective' ? '0.4' : '';
}
eventTypeSelect.addEventListener('change', updateParamVisibility);

// ── Analyze ─────────────────────────────────────────────────────────────────
analyzeBtn.addEventListener('click', async () => {
  if (!currentDem) { setLog('Load a match first.', 'error'); return; }

  setLog('<span class="spinner"></span>Analyzing…');
  analyzeBtn.disabled = true;

  const eventType = eventTypeSelect.value;
  const tickRate = parsedData?.tick_rate || 64;
  const body = {
    dem_path:     currentDem,
    steam_id:     document.getElementById('player-select').value,
    event_type:   eventType,
    lead_ticks:   Math.round(Number(document.getElementById('lead').value) * tickRate),
    buffer_ticks: Math.round(Number(document.getElementById('buffer').value) * tickRate),
  };
  if (eventType === 'multikill') {
    body.window_seconds = Number(document.getElementById('window').value);
    body.threshold      = Number(document.getElementById('threshold').value);
  } else if (eventType === 'kill_streak') {
    body.streak_threshold = Number(document.getElementById('streak-threshold').value);
  } else if (eventType === 'objective') {
    body.objective_types = [...document.getElementById('obj-types').selectedOptions].map(o => o.value);
  }

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (data.status !== 'ok') { setLog(`Error: ${data.message}`, 'error'); return; }

    clips = data.clips;
    renderClips();

    const n = clips.length;
    setLog(`Found ${n} clip zone${n !== 1 ? 's' : ''}.`, n > 0 ? 'ok' : '');

  } catch (err) {
    setLog(`Unexpected error: ${err.message}`, 'error');
  } finally {
    analyzeBtn.disabled = false;
  }
});

// ── Render clips ────────────────────────────────────────────────────────────
function clearClips() {
  clips = [];
  Object.keys(clipStates).forEach(k => delete clipStates[k]);
  contentEl.innerHTML = '';
  contentEl.appendChild(emptyState);
  emptyState.style.display = '';
  captureBar.classList.remove('visible');
  if (currentPage === 'recording') renderRecordingQueue();
}

function renderClips() {
  contentEl.innerHTML = '';
  const tickRate = parsedData?.tick_rate || 64;

  if (clips.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.innerHTML = `<div class="icon">🔍</div><p>No clip zones found with the current settings. Try lowering the threshold or increasing the time window.</p>`;
    contentEl.appendChild(empty);
    captureBar.classList.remove('visible');
    return;
  }

  const header = document.createElement('div');
  header.className = 'clips-header';
  header.innerHTML = `<h2>Clip Zones</h2><span class="count">${clips.length} found</span>`;
  contentEl.appendChild(header);

  const list = document.createElement('div');
  list.className = 'clip-list';

  clips.forEach(clip => {
    const startTime = ticksToTime(clip.start_tick, tickRate);
    const endTime   = ticksToTime(clip.end_tick, tickRate);
    const durationS = ((clip.end_tick - clip.start_tick) / tickRate).toFixed(1);

    let midLine, bottomLine;
    if (clip.event_type === 'objective') {
      midLine    = `<span class="dur">${durationS}s</span>`;
      bottomLine = `<span class="kills">${clip.detail}</span>`;
    } else if (clip.event_type === 'single_kill') {
      midLine    = `<span class="dur">${durationS}s · 1 kill</span>`;
      bottomLine = `<span class="kills">${clip.detail}</span>`;
    } else {
      const killTimes = clip.kill_ticks.map(t => ticksToTime(t, tickRate)).join(', ');
      midLine    = `<span class="dur">${durationS}s · ${clip.kill_count} kills</span>`;
      bottomLine = clip.detail
        ? `<span class="kills">${clip.detail}</span>`
        : `<span class="kills">At: ${killTimes}</span>`;
    }

    const card = document.createElement('div');
    card.className = 'clip-card selected';
    card.dataset.clipId = clip.clip_id;
    card.innerHTML = `
      <input type="checkbox" checked data-id="${clip.clip_id}" />
      <span class="clip-id">${clip.clip_id}</span>
      <span class="clip-badge ${clipBadgeClass(clip)}">${clip.reason}</span>
      <div class="clip-times">
        <span class="ts">${startTime} → ${endTime}</span>
        ${midLine}
        ${bottomLine}
      </div>
    `;

    card.addEventListener('click', e => {
      const cb = card.querySelector('input[type="checkbox"]');
      if (e.target !== cb) cb.checked = !cb.checked;
      card.classList.toggle('selected', cb.checked);
      updateCaptureBar();
    });

    list.appendChild(card);
  });

  contentEl.appendChild(list);
  captureBar.classList.add('visible');
  updateCaptureBar();
  if (currentPage === 'recording') renderRecordingQueue();
}

function updateCaptureBar() {
  const checked = contentEl.querySelectorAll('input[type="checkbox"]:checked').length;
  selCount.textContent = `${checked} clip${checked !== 1 ? 's' : ''} selected`;
  captureBtn.disabled = checked === 0;
}

deselectBtn.addEventListener('click', () => {
  contentEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.checked = false;
    cb.closest('.clip-card').classList.remove('selected');
  });
  updateCaptureBar();
});

captureBtn.addEventListener('click', () => {
  switchPage('recording');
});

// ── Recording page state ───────────────────────────────────────────────────
let currentPage = 'analysis';
const clipStates = {};  // clip_id -> {status, message}

// ── Page navigation ────────────────────────────────────────────────────────
function switchPage(page) {
  currentPage = page;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.page === page);
  });
  document.getElementById('main-analysis').style.display           = page === 'analysis'  ? 'flex' : 'none';
  document.getElementById('main-recording').style.display          = page === 'recording' ? 'flex' : 'none';
  document.getElementById('page-analysis-sidebar').style.display   = page === 'analysis'  ? '' : 'none';
  document.getElementById('page-recording-sidebar').style.display  = page === 'recording' ? '' : 'none';
  if (page === 'recording') {
    renderRecordingQueue();
    pollObsStatus();
  }
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => switchPage(btn.dataset.page));
});

// ── OBS status polling ─────────────────────────────────────────────────────
async function pollObsStatus() {
  try {
    const res  = await fetch('/api/obs/status');
    const data = await res.json();
    updateObsBadge(data.connected, data.recording);
    if (data.config_defaults && !document.getElementById('obs-host').dataset.loaded) {
      const d = data.config_defaults;
      document.getElementById('obs-host').value  = d.host  || 'localhost';
      document.getElementById('obs-port').value  = d.port  || 4455;
      if (d.password)            document.getElementById('obs-password').value  = d.password;
      if (d.steam_exe)           document.getElementById('steam-exe').value     = d.steam_exe;
      if (d.launch_wait_seconds) document.getElementById('launch-wait').value   = d.launch_wait_seconds;
      if (d.seek_settle_seconds) document.getElementById('seek-settle').value   = d.seek_settle_seconds;
      document.getElementById('obs-host').dataset.loaded = '1';
    }
  } catch { /* ignore */ }
}

function updateObsBadge(connected, recording) {
  const badge      = document.getElementById('obs-status-badge');
  const statusText = document.getElementById('obs-status-text');
  if (!connected) {
    badge.className = 'status-badge badge-disconnected';
    badge.innerHTML = '<span class="status-dot"></span> Disconnected';
    statusText.textContent = 'Not connected';
  } else if (recording) {
    badge.className = 'status-badge badge-recording-obs';
    badge.innerHTML = '<span class="status-dot"></span> Recording';
    statusText.textContent = 'Connected · Recording';
  } else {
    badge.className = 'status-badge badge-connected';
    badge.innerHTML = '<span class="status-dot"></span> Connected';
    statusText.textContent = 'Connected · Idle';
  }
  document.getElementById('obs-disconnect-btn').disabled = !connected;
  document.getElementById('obs-connect-btn').disabled    = connected;
  document.getElementById('rec-start-btn').disabled      = !connected || recording;
  document.getElementById('rec-stop-btn').disabled       = !connected || !recording;
}

document.getElementById('obs-connect-btn').addEventListener('click', async () => {
  setRecLog('<span class="spinner"></span>Connecting…');
  document.getElementById('obs-connect-btn').disabled = true;
  try {
    const res  = await fetch('/api/obs/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host:     document.getElementById('obs-host').value,
        port:     Number(document.getElementById('obs-port').value),
        password: document.getElementById('obs-password').value,
      }),
    });
    const data = await res.json();
    if (data.status === 'ok') {
      setRecLog(data.message, 'ok');
      pollObsStatus();
    } else {
      setRecLog(`Error: ${data.message}`, 'error');
      document.getElementById('obs-connect-btn').disabled = false;
    }
  } catch (err) {
    setRecLog(`Unexpected error: ${err.message}`, 'error');
    document.getElementById('obs-connect-btn').disabled = false;
  }
});

document.getElementById('obs-disconnect-btn').addEventListener('click', async () => {
  await fetch('/api/obs/disconnect', { method: 'POST' });
  updateObsBadge(false, false);
  setRecLog('Disconnected.');
});

document.getElementById('rec-start-btn').addEventListener('click', async () => {
  const res  = await fetch('/api/record/start', { method: 'POST' });
  const data = await res.json();
  if (data.status === 'ok') {
    setRecLog('Recording started.', 'ok');
    updateObsBadge(true, true);
    pollObsStatus();
  } else { setRecLog(`Error: ${data.message}`, 'error'); }
});

document.getElementById('rec-stop-btn').addEventListener('click', async () => {
  const res  = await fetch('/api/record/stop', { method: 'POST' });
  const data = await res.json();
  if (data.status === 'ok') {
    setRecLog(data.output_path ? `Saved: ${data.output_path}` : 'Recording stopped.', 'ok');
    updateObsBadge(true, false);
    pollObsStatus();
  } else { setRecLog(`Error: ${data.message}`, 'error'); }
});

// ── Rec log ────────────────────────────────────────────────────────────────
const recLogEl = document.getElementById('rec-log');
function setRecLog(msg, type = '') {
  recLogEl.className = type;
  recLogEl.innerHTML = msg;
}

// ── Recording queue ────────────────────────────────────────────────────────
function renderRecordingQueue() {
  const container = document.getElementById('rec-content');
  const barEl     = document.getElementById('record-all-bar');

  container.innerHTML = '';

  if (!clips.length) {
    const emptyEl = document.createElement('div');
    emptyEl.className = 'empty-state';
    emptyEl.innerHTML = '<div class="icon">🎞</div><p>Run <strong>Find Clips</strong> on the Analysis page first. Clips will appear here for recording.</p>';
    container.appendChild(emptyEl);
    barEl.style.display = 'none';
    return;
  }

  const tickRate = parsedData?.tick_rate || 64;

  const header = document.createElement('div');
  header.className = 'clips-header';
  header.innerHTML = `<h2>Clip Queue</h2><span class="count">${clips.length} clips</span>`;
  container.appendChild(header);

  const list = document.createElement('div');
  list.className = 'clip-list';

  clips.forEach(clip => {
    const state     = clipStates[clip.clip_id] || { status: 'queued' };
    const startTime = ticksToTime(clip.start_tick, tickRate);
    const endTime   = ticksToTime(clip.end_tick,   tickRate);
    const durationS = ((clip.end_tick - clip.start_tick) / tickRate).toFixed(1);
    const isBusy    = state.status !== 'queued' && state.status !== 'done' && state.status !== 'error';

    const card = document.createElement('div');
    card.className = 'rec-clip-card';
    card.dataset.clipId = clip.clip_id;
    card.innerHTML = `
      <input type="checkbox" class="rec-checkbox" data-id="${clip.clip_id}" ${isBusy ? 'disabled' : ''} />
      <span class="clip-id">${clip.clip_id}</span>
      <span class="clip-badge ${clipBadgeClass(clip)}">${clip.reason}</span>
      <div class="clip-times" style="flex:1">
        <span class="ts">${startTime} → ${endTime}</span>
        <span class="dur">${durationS}s</span>
      </div>
      <span class="clip-status clip-status-${state.status}" title="${state.message || ''}">${state.status}</span>
      <button class="btn btn-secondary btn-sm" onclick="prepareClip('${clip.clip_id}')" ${isBusy ? 'disabled' : ''}>Prepare</button>
      <button class="btn btn-primary btn-sm" onclick="recordClip('${clip.clip_id}')" ${isBusy ? 'disabled' : ''}>Record</button>
    `;
    list.appendChild(card);
  });

  container.appendChild(list);
  barEl.style.display = 'flex';
  updateRecSelCount();
}

function updateRecSelCount() {
  const checked = document.querySelectorAll('.rec-checkbox:checked').length;
  document.getElementById('rec-sel-count').textContent =
    `${checked} clip${checked !== 1 ? 's' : ''} selected`;
}

function setClipState(clipId, status, message = '') {
  clipStates[clipId] = { status, message };
  const card = document.querySelector(`.rec-clip-card[data-clip-id="${clipId}"]`);
  if (!card) return;
  const badge = card.querySelector('.clip-status');
  if (badge) {
    badge.className   = `clip-status clip-status-${status}`;
    badge.textContent = status;
    badge.title       = message;
  }
  const isBusy = status !== 'queued' && status !== 'done' && status !== 'error';
  card.querySelectorAll('button, .rec-checkbox').forEach(el => { el.disabled = isBusy; });
}

// ── Clip pipeline actions ──────────────────────────────────────────────────
async function prepareClip(clipId) {
  if (!currentDem) { setRecLog('No demo loaded.', 'error'); return null; }
  const clip = clips.find(c => c.clip_id === clipId);
  if (!clip) return null;
  setClipState(clipId, 'preparing', 'Launching game...');
  setRecLog(`Preparing clip ${clipId}…`);
  try {
    const res  = await fetch('/api/record/prepare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dem_path:    currentDem,
        start_tick:  clip.start_tick,
        end_tick:    clip.end_tick,
        player_name: clip.player_name || '',
        steam_exe:   document.getElementById('steam-exe').value,
        launch_wait: Number(document.getElementById('launch-wait').value),
        seek_settle: Number(document.getElementById('seek-settle').value),
      }),
    });
    const data = await res.json();
    if (data.status === 'started') {
      pollJob(data.job_id, clipId);
      return data.job_id;
    }
    setClipState(clipId, 'error', data.message);
    setRecLog(`Error: ${data.message}`, 'error');
    return null;
  } catch (err) {
    setClipState(clipId, 'error', err.message);
    setRecLog(`Unexpected error: ${err.message}`, 'error');
    return null;
  }
}

async function recordClip(clipId) {
  if (!currentDem) { setRecLog('No demo loaded.', 'error'); return null; }
  const clip = clips.find(c => c.clip_id === clipId);
  if (!clip) return null;
  setClipState(clipId, 'preparing', 'Starting pipeline...');
  setRecLog(`Recording clip ${clipId}…`);
  try {
    const res  = await fetch('/api/record/clip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dem_path:   currentDem,
        clip,
        steam_exe:  document.getElementById('steam-exe').value,
        launch_wait: Number(document.getElementById('launch-wait').value),
        seek_settle: Number(document.getElementById('seek-settle').value),
      }),
    });
    const data = await res.json();
    if (data.status === 'started') {
      pollJob(data.job_id, clipId);
      return data.job_id;
    }
    setClipState(clipId, 'error', data.message);
    setRecLog(`Error: ${data.message}`, 'error');
    return null;
  } catch (err) {
    setClipState(clipId, 'error', err.message);
    setRecLog(`Unexpected error: ${err.message}`, 'error');
    return null;
  }
}

function pollJob(jobId, clipId) {
  const timer = setInterval(async () => {
    try {
      const res = await fetch(`/api/record/job/${jobId}`);
      if (res.status === 404) { clearInterval(timer); return; }
      const job = await res.json();
      setClipState(clipId, job.status, job.message || '');
      if (job.message) setRecLog(`Clip ${clipId}: ${job.message}`);
      if (job.status === 'done') {
        clearInterval(timer);
        setRecLog(job.output_path ? `Clip ${clipId} saved: ${job.output_path}` : `Clip ${clipId} done.`, 'ok');
        pollObsStatus();
      } else if (job.status === 'error') {
        clearInterval(timer);
        setRecLog(`Clip ${clipId} failed: ${job.message}`, 'error');
        pollObsStatus();
      }
    } catch { /* network glitch, keep polling */ }
  }, 2000);
}

function waitForJob(jobId) {
  return new Promise(resolve => {
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/record/job/${jobId}`);
        if (res.status === 404) { clearInterval(timer); resolve({ status: 'error' }); return; }
        const job = await res.json();
        if (job.status === 'done' || job.status === 'error') {
          clearInterval(timer);
          resolve(job);
        }
      } catch { /* keep waiting */ }
    }, 2000);
  });
}

document.getElementById('select-all-btn').addEventListener('click', () => {
  const checkboxes = [...document.querySelectorAll('.rec-checkbox:not(:disabled)')];
  const allChecked = checkboxes.every(cb => cb.checked);
  checkboxes.forEach(cb => { cb.checked = !allChecked; });
  document.getElementById('select-all-btn').textContent = allChecked ? 'Select All' : 'Deselect All';
  updateRecSelCount();
});

document.getElementById('prepare-selected-btn').addEventListener('click', async () => {
  const selected = [...document.querySelectorAll('.rec-checkbox:checked')].map(cb => cb.dataset.id);
  if (!selected.length) return;
  document.getElementById('prepare-selected-btn').disabled = true;
  document.getElementById('record-selected-btn').disabled  = true;
  for (const clipId of selected) {
    const jobId = await prepareClip(clipId);
    if (jobId) await waitForJob(jobId);
  }
  document.getElementById('prepare-selected-btn').disabled = false;
  document.getElementById('record-selected-btn').disabled  = false;
  setRecLog('Batch prepare complete.', 'ok');
});

document.getElementById('record-selected-btn').addEventListener('click', async () => {
  const selected = [...document.querySelectorAll('.rec-checkbox:checked')].map(cb => cb.dataset.id);
  if (!selected.length) return;
  document.getElementById('prepare-selected-btn').disabled = true;
  document.getElementById('record-selected-btn').disabled  = true;
  for (const clipId of selected) {
    const jobId = await recordClip(clipId);
    if (jobId) await waitForJob(jobId);
  }
  document.getElementById('prepare-selected-btn').disabled = false;
  document.getElementById('record-selected-btn').disabled  = false;
  setRecLog('Batch recording complete.', 'ok');
});

// ── Init ───────────────────────────────────────────────────────────────────
loadFileList();
