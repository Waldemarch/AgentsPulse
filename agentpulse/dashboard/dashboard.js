const colors = {
    claude: '#2f7ef7',
    codex: '#13a579',
    kimi: '#a855f7',
    seven: '#8b5cf6',
    warn: '#d64545',
};
// Weekly series get a darker shade of the provider's colour so the two lines
// of one provider stay visually related but distinguishable.
const weeklyColors = { codex: '#0f766e', kimi: '#7e22ce' };

function themeColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

// Per-run session token passed by the tray app in the URL; required for POST
// endpoints so pages from other origins cannot forge settings or test-event
// requests. Kept in sessionStorage and stripped from the address bar.
const authToken = (() => {
    const params = new URLSearchParams(location.search);
    const fromUrl = params.get('token');
    if (fromUrl) {
        sessionStorage.setItem('agentpulse-token', fromUrl);
        params.delete('token');
        const query = params.toString();
        history.replaceState(null, '', location.pathname + (query ? `?${query}` : ''));
        return fromUrl;
    }
    return sessionStorage.getItem('agentpulse-token') || '';
})();

async function postJson(path, payload) {
    const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-AgentsPulse-Token': authToken },
        body: JSON.stringify(payload),
    });
    if (response.status === 403) {
        return { ok: false, errors: [tr('session_expired', 'session expired - reopen the dashboard from the tray menu')] };
    }
    return response.json();
}

let state = { status: null, history: null, range: '24h' };

// Localized strings fetched from /api/i18n; empty until loadI18n() resolves,
// so every lookup falls back to the English text baked into the markup.
let t = {};

function tr(key, fallback) {
    const value = t[key];
    return value === undefined ? fallback : value;
}

function fmt(template, vars) {
    return String(template).replace(/\{(\w+)\}/g, (match, name) => (name in vars ? vars[name] : match));
}

async function loadI18n() {
    try {
        t = await fetch('/api/i18n', { cache: 'no-store' }).then(r => r.json());
    } catch {
        t = {};
    }
    applyI18n();
}

function applyI18n() {
    for (const el of document.querySelectorAll('[data-i18n]')) {
        const value = t[el.dataset.i18n];
        if (value !== undefined) el.textContent = value;
    }
}

const rangeSelect = document.getElementById('rangeSelect');
const exportCsv = document.getElementById('exportCsv');

rangeSelect.addEventListener('change', () => {
    state.range = rangeSelect.value;
    exportCsv.href = `/api/history.csv?range=${encodeURIComponent(state.range)}`;
    refresh();
});

async function fetchJson(path) {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        error.status = response.status;
        throw error;
    }
    return response.json();
}

function setConnectionError(error) {
    const banner = document.getElementById('connectionError');
    if (!error) {
        banner.hidden = true;
        return;
    }
    const key = error.status === 403 ? 'session_expired' : 'connection_lost';
    const fallback = error.status === 403
        ? 'session expired - reopen the dashboard from the tray menu'
        : 'Connection lost - retrying';
    banner.textContent = tr(key, fallback);
    banner.hidden = false;
}

async function refresh() {
    try {
        const [status, history] = await Promise.all([
            fetchJson('/api/status'),
            fetchJson(`/api/history?range=${encodeURIComponent(state.range)}`),
        ]);
        state = { ...state, status, history };
        setConnectionError(null);
        render();
    } catch (error) {
        setConnectionError(error);
    }
}

async function loadSettings() {
    let data;
    try {
        const response = await fetch('/api/settings', { cache: 'no-store', headers: { 'X-AgentsPulse-Token': authToken } });
        if (!response.ok) return;
        data = await response.json();
    } catch {
        return;
    }
    const s = data.settings || {};
    document.getElementById('autostartEnabled').checked = !!s.autostart;
    document.getElementById('codexEnabled').checked = !!s.codex_enabled;
    document.getElementById('kimiEnabled').checked = !!s.kimi_enabled;
    document.getElementById('tooltipFields').value = (s.tooltip_fields || []).join(', ');
    document.getElementById('thresholdClaude5h').value = (s.alert_thresholds_five_hour || []).join(', ');
    document.getElementById('thresholdClaude7d').value = (s.alert_thresholds_seven_day || []).join(', ');
    document.getElementById('thresholdCodex5h').value = (s.alert_thresholds_codex_five_hour || []).join(', ');
    document.getElementById('thresholdCodex7d').value = (s.alert_thresholds_codex_seven_day || []).join(', ');
    document.getElementById('thresholdKimi5h').value = (s.alert_thresholds_kimi_five_hour || []).join(', ');
    document.getElementById('thresholdKimi7d').value = (s.alert_thresholds_kimi_seven_day || []).join(', ');
    document.getElementById('predictionEnabled').checked = s.prediction_enabled !== false;
    document.getElementById('predictionDayEnd').value = s.prediction_day_end_time || '18:00';
    document.getElementById('heatmapEnabled').checked = s.heatmap_enabled !== false;
    document.getElementById('quietHoursEnabled').checked = !!s.quiet_hours_enabled;
    document.getElementById('quietHoursStart').value = s.quiet_hours_start || '22:00';
    document.getElementById('quietHoursEnd').value = s.quiet_hours_end || '08:00';
    document.getElementById('resetCommand').value = (s.on_reset_command || []).join(' && ');
    document.getElementById('thresholdCommand').value = (s.on_threshold_command || []).join(' && ');
}

function render() {
    if (!state.status || !state.history) return;
    renderProviders(state.status.providers || []);
    renderDiagnostics(state.status);
    drawUsageChart(document.getElementById('usageChart'), state.history.rows || []);
    drawBurnChart(document.getElementById('burnChart'), state.history.rows || []);
    renderPredictions(state.status);
    renderHeatmap(state.history.rows || [], state.status.settings || {});
    document.getElementById('historyMeta').textContent = fmt(tr('rows', '{count} rows · {range}'), { count: state.history.rows.length, range: state.range });
}

function renderProviders(providers) {
    const root = document.getElementById('providers');
    root.replaceChildren(...providers.map(providerCard));
}

function providerCard(provider) {
    const card = document.createElement('article');
    card.className = 'provider-card';

    const title = document.createElement('div');
    title.className = 'provider-title';
    title.innerHTML = `<h2>${escapeHtml(provider.label)}</h2><span>${formatUpdated(provider.last_success_time)}</span>`;
    card.appendChild(title);

    if (provider.error) {
        const err = document.createElement('p');
        err.className = 'error';
        err.textContent = provider.error;
        card.appendChild(err);
    }

    const list = document.createElement('div');
    list.className = 'usage-list';
    for (const entry of provider.usage) {
        const item = document.createElement('div');
        const pct = Math.round(entry.utilization);
        item.innerHTML = `
            <div class="metric-row">
                <span>${escapeHtml(entry.label)}</span>
                <strong>${pct}%</strong>
            </div>
            <div class="bar"><div class="fill ${pct >= 100 ? 'warn' : pct >= 80 ? 'high' : ''}" style="width:${Math.min(100, Math.max(0, pct))}%"></div></div>
            <p class="muted">${escapeHtml(metricSubtext(entry))}</p>
        `;
        list.appendChild(item);
    }
    if (!provider.usage.length && !provider.error) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = tr('waiting_usage', 'Waiting for usage data');
        list.appendChild(empty);
    }
    card.appendChild(list);
    return card;
}

function metricSubtext(entry) {
    const parts = [entry.reset_text || tr('no_reset', 'No reset time')];
    if (entry.burn) {
        const pace = entry.burn.healthy ? tr('pace_healthy', 'on pace') : tr('pace_ahead', 'ahead of pace');
        if (entry.burn.eta_seconds) parts.push(`ETA ${formatCountdown(entry.burn.eta_seconds)}`);
        parts.push(`${Math.round(entry.burn.burn_per_hour * 10) / 10} pp/h`);
        parts.push(pace);
    }
    return parts.join(' · ');
}

function renderDiagnostics(status) {
    const root = document.getElementById('diagnostics');
    const cards = [
        [tr('diag_app', 'App'), `${status.app.name} ${status.app.version}`],
        [tr('diag_bind', 'Dashboard bind'), status.privacy.bind],
        [tr('diag_analytics', 'Analytics'), status.privacy.analytics ? tr('enabled', 'enabled') : tr('disabled', 'disabled')],
        [tr('diag_tokens', 'Token payloads'), status.privacy.token_free ? tr('not_exposed', 'not exposed') : tr('check_config', 'check configuration')],
        [tr('diag_next_update', 'Next update'), status.next_poll_time ? formatCountdown(status.next_poll_time - Date.now() / 1000) : tr('unknown', 'unknown')],
    ];
    for (const provider of status.providers || []) {
        const versions = (provider.installations || []).map(i => `${i.name} ${i.version}`).join(', ') || tr('not_detected', 'not detected');
        cards.push([fmt(tr('cli', '{label} CLI'), { label: provider.label }), versions]);
    }
    root.replaceChildren(...cards.map(([k, v]) => {
        const div = document.createElement('div');
        div.className = 'diag';
        div.innerHTML = `<div class="muted">${escapeHtml(k)}</div><div>${escapeHtml(v)}</div>`;
        return div;
    }));
}

function drawUsageChart(canvas, rows) {
    const points = rows.filter(r => r.utilization !== null && (r.field === 'five_hour' || r.field === 'seven_day'));
    drawLineChart(canvas, points, p => p.utilization, p => `${p.provider}:${p.field}`, 'Usage %');
}

function drawBurnChart(canvas, rows) {
    const points = [];
    const groups = groupRows(rows.filter(r => r.utilization !== null));
    for (const groupRows of Object.values(groups)) {
        groupRows.sort((a, b) => a.ts - b.ts);
        for (let i = 1; i < groupRows.length; i++) {
            const prev = groupRows[i - 1];
            const cur = groupRows[i];
            const hours = (cur.ts - prev.ts) / 3600;
            if (hours <= 0) continue;
            points.push({ ...cur, burn: Math.max(-100, Math.min(100, (cur.utilization - prev.utilization) / hours)) });
        }
    }
    document.getElementById('burnMeta').textContent = tr('pp_per_hour', 'percentage points per hour');
    drawLineChart(canvas, points, p => p.burn, p => `${p.provider}:${p.field}`, 'pp/h', { minY: -10, maxY: 60 });
}

function renderPredictions(status) {
    const settings = status.settings || {};
    const section = document.getElementById('predictionSection');
    section.hidden = settings.prediction_enabled === false;
    if (section.hidden) return;

    const root = document.getElementById('predictions');
    const cards = [];
    const target = settings.prediction_day_end_time || '18:00';
    const hoursToDayEnd = hoursUntilLocalTime(target);

    for (const provider of status.providers || []) {
        for (const entry of provider.usage || []) {
            if (!entry.burn || !Number.isFinite(entry.burn.burn_per_hour)) continue;
            const dayPct = Math.min(999, entry.utilization + entry.burn.burn_per_hour * hoursToDayEnd);
            const resetHours = secondsUntilIso(entry.resets_at) / 3600;
            const periodPct = Math.min(999, entry.utilization + entry.burn.burn_per_hour * resetHours);
            cards.push({
                title: `${provider.label} ${entry.label}`,
                day: fmt(tr('by_time', '{pct}% by {time}'), { pct: Math.round(dayPct), time: target }),
                period: fmt(tr('by_reset', '{pct}% by reset'), { pct: Math.round(periodPct) }),
                tone: dayPct >= 100 || periodPct >= 100 ? 'warn' : 'ok',
            });
        }
    }

    document.getElementById('predictionMeta').textContent = fmt(tr('day_target', 'local day target {target}'), { target });
    root.replaceChildren(...cards.map(card => {
        const div = document.createElement('div');
        div.className = `prediction ${card.tone}`;
        div.innerHTML = `
            <div class="muted">${escapeHtml(card.title)}</div>
            <strong>${escapeHtml(card.day)}</strong>
            <span>${escapeHtml(card.period)}</span>
        `;
        return div;
    }));
    if (!cards.length) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = tr('waiting_enough', 'Waiting for enough usage data');
        root.replaceChildren(empty);
    }
}

function renderHeatmap(rows, settings) {
    const section = document.getElementById('heatmapSection');
    section.hidden = settings.heatmap_enabled === false;
    if (section.hidden) return;

    const root = document.getElementById('heatmap');
    const buckets = {};
    const groups = groupRows(rows.filter(r => r.utilization !== null));
    for (const group of Object.values(groups)) {
        group.sort((a, b) => a.ts - b.ts);
        for (let i = 1; i < group.length; i++) {
            const prev = group[i - 1];
            const cur = group[i];
            const delta = cur.utilization - prev.utilization;
            if (delta <= 0) continue;
            const hour = new Date(cur.ts * 1000).getHours();
            const key = `${cur.provider}:${hour}`;
            buckets[key] = (buckets[key] || 0) + delta;
        }
    }

    const max = Math.max(1, ...Object.values(buckets));
    const providers = [...new Set(rows.filter(r => r.provider).map(r => r.provider))].sort();
    const nodes = [];
    for (const provider of providers) {
        const label = document.createElement('div');
        label.className = 'heatmap-label';
        label.textContent = provider;
        nodes.push(label);
        for (let hour = 0; hour < 24; hour++) {
            const value = buckets[`${provider}:${hour}`] || 0;
            const cell = document.createElement('div');
            cell.className = 'heatmap-cell';
            cell.title = `${provider} ${String(hour).padStart(2, '0')}:00 · ${Math.round(value * 10) / 10} pp`;
            cell.style.opacity = String(0.18 + 0.82 * value / max);
            cell.textContent = hour % 6 === 0 ? String(hour) : '';
            nodes.push(cell);
        }
    }
    document.getElementById('heatmapMeta').textContent = tr('heatmap_meta', 'positive usage deltas by local hour');
    root.replaceChildren(...(nodes.length ? nodes : [emptyMuted(tr('waiting_history', 'Waiting for history data'))]));
}

function drawLineChart(canvas, points, getY, getKey, label, fixed = {}) {
    const ctx = canvas.getContext('2d');
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(320, Math.floor(rect.width * ratio));
    canvas.height = Math.floor(canvas.getAttribute('height') * ratio);
    ctx.scale(ratio, ratio);

    const w = canvas.width / ratio;
    const h = canvas.height / ratio;
    ctx.clearRect(0, 0, w, h);

    const pad = { l: 42, r: 14, t: 14, b: 28 };
    ctx.strokeStyle = themeColor('--chart-grid', '#d9dee7');
    ctx.fillStyle = themeColor('--chart-text', '#667085');
    ctx.font = '12px Segoe UI, sans-serif';

    for (let i = 0; i <= 4; i++) {
        const y = pad.t + (h - pad.t - pad.b) * i / 4;
        ctx.beginPath();
        ctx.moveTo(pad.l, y);
        ctx.lineTo(w - pad.r, y);
        ctx.stroke();
    }

    if (!points.length) {
        ctx.fillText(tr('waiting_history', 'Waiting for history data'), pad.l, h / 2);
        return;
    }

    const minTs = Math.min(...points.map(p => p.ts));
    const maxTs = Math.max(...points.map(p => p.ts));
    const ys = points.map(getY);
    const minY = fixed.minY ?? Math.min(0, ...ys);
    const maxY = fixed.maxY ?? Math.max(100, ...ys);

    ctx.fillText(label, 8, 18);
    ctx.fillText(`${Math.round(maxY)}`, 8, pad.t + 4);
    ctx.fillText(`${Math.round(minY)}`, 8, h - pad.b);

    const groups = groupRows(points, getKey);
    let idx = 0;
    for (const [key, group] of Object.entries(groups)) {
        group.sort((a, b) => a.ts - b.ts);
        ctx.strokeStyle = lineColor(key, idx++);
        ctx.lineWidth = 2;
        ctx.beginPath();
        group.forEach((p, i) => {
            const x = map(p.ts, minTs, maxTs || minTs + 1, pad.l, w - pad.r);
            const y = map(getY(p), minY, maxY || minY + 1, h - pad.b, pad.t);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
    }
}

function groupRows(rows, keyFn = r => `${r.provider}:${r.field}`) {
    return rows.reduce((acc, row) => {
        const key = keyFn(row);
        (acc[key] ||= []).push(row);
        return acc;
    }, {});
}

function lineColor(key, index) {
    const provider = Object.keys(weeklyColors).find((name) => key.includes(name));
    if (provider) return key.includes('seven') ? weeklyColors[provider] : colors[provider];
    if (key.includes('seven')) return colors.seven;
    return [colors.claude, '#f59e0b', '#db2777', '#475569'][index % 4];
}

function map(value, inMin, inMax, outMin, outMax) {
    if (inMax === inMin) return (outMin + outMax) / 2;
    return outMin + (value - inMin) * (outMax - outMin) / (inMax - inMin);
}

function formatUpdated(ts) {
    if (!ts) return tr('waiting', 'waiting');
    return fmt(tr('ago', '{duration} ago'), { duration: formatCountdown(Date.now() / 1000 - ts) });
}

function formatCountdown(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
}

function hoursUntilLocalTime(value) {
    const [h, m] = String(value || '18:00').split(':').map(Number);
    const now = new Date();
    const target = new Date(now);
    target.setHours(Number.isFinite(h) ? h : 18, Number.isFinite(m) ? m : 0, 0, 0);
    if (target <= now) target.setDate(target.getDate() + 1);
    return (target - now) / 3600000;
}

function secondsUntilIso(value) {
    const ts = Date.parse(value || '');
    if (!Number.isFinite(ts)) return 0;
    return Math.max(0, (ts - Date.now()) / 1000);
}

function emptyMuted(text) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = text;
    return p;
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function parseList(value) {
    return value.split(',').map(s => s.trim()).filter(Boolean);
}

function parseNumbers(value) {
    return parseList(value).map(Number).filter(n => Number.isFinite(n));
}

document.getElementById('settingsForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = {
        autostart: document.getElementById('autostartEnabled').checked,
        codex_enabled: document.getElementById('codexEnabled').checked,
        kimi_enabled: document.getElementById('kimiEnabled').checked,
        tooltip_fields: parseList(document.getElementById('tooltipFields').value),
        alert_thresholds_five_hour: parseNumbers(document.getElementById('thresholdClaude5h').value),
        alert_thresholds_seven_day: parseNumbers(document.getElementById('thresholdClaude7d').value),
        alert_thresholds_codex_five_hour: parseNumbers(document.getElementById('thresholdCodex5h').value),
        alert_thresholds_codex_seven_day: parseNumbers(document.getElementById('thresholdCodex7d').value),
        alert_thresholds_kimi_five_hour: parseNumbers(document.getElementById('thresholdKimi5h').value),
        alert_thresholds_kimi_seven_day: parseNumbers(document.getElementById('thresholdKimi7d').value),
        prediction_enabled: document.getElementById('predictionEnabled').checked,
        prediction_day_end_time: document.getElementById('predictionDayEnd').value || '18:00',
        heatmap_enabled: document.getElementById('heatmapEnabled').checked,
        quiet_hours_enabled: document.getElementById('quietHoursEnabled').checked,
        quiet_hours_start: document.getElementById('quietHoursStart').value || '22:00',
        quiet_hours_end: document.getElementById('quietHoursEnd').value || '08:00',
        on_reset_command: document.getElementById('resetCommand').value.trim(),
        on_threshold_command: document.getElementById('thresholdCommand').value.trim(),
    };
    const result = await postJson('/api/settings', payload);
    document.getElementById('settingsStatus').textContent = result.ok
        ? fmt(tr('saved', 'saved to {path}; restart required'), { path: result.path })
        : fmt(tr('error', 'error: {errors}'), { errors: (result.errors || []).join(', ') });
});

document.getElementById('testReset').addEventListener('click', () => testEvent('reset'));
document.getElementById('testThreshold').addEventListener('click', () => testEvent('threshold'));

async function testEvent(event) {
    const result = await postJson('/api/test-event', { event });
    document.getElementById('settingsStatus').textContent = result.ok
        ? fmt(tr('test_fired', 'test {event} fired'), { event })
        : fmt(tr('test_failed', 'test failed: {errors}'), { errors: (result.errors || []).join(', ') || tr('unknown_error', 'unknown error') });
}

loadI18n().then(() => {
    refresh();
    loadSettings();
});
setInterval(refresh, 15000);
