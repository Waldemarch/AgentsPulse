const colors = {
    claude: '#1f7aec',
    codex: '#13a579',
    seven: '#8b5cf6',
    warn: '#d64545',
    grid: '#d9dee7',
    text: '#20242a',
    dim: '#667085',
};

let state = { status: null, history: null, range: '24h' };

const rangeSelect = document.getElementById('rangeSelect');
const exportCsv = document.getElementById('exportCsv');

rangeSelect.addEventListener('change', () => {
    state.range = rangeSelect.value;
    exportCsv.href = `/api/history.csv?range=${encodeURIComponent(state.range)}`;
    refresh();
});

async function refresh() {
    const [status, history] = await Promise.all([
        fetch('/api/status', { cache: 'no-store' }).then(r => r.json()),
        fetch(`/api/history?range=${encodeURIComponent(state.range)}`, { cache: 'no-store' }).then(r => r.json()),
    ]);
    state = { ...state, status, history };
    render();
}

async function loadSettings() {
    const data = await fetch('/api/settings', { cache: 'no-store' }).then(r => r.json());
    const s = data.settings || {};
    document.getElementById('codexEnabled').checked = !!s.codex_enabled;
    document.getElementById('tooltipFields').value = (s.tooltip_fields || []).join(', ');
    document.getElementById('thresholdClaude5h').value = (s.alert_thresholds_five_hour || []).join(', ');
    document.getElementById('thresholdClaude7d').value = (s.alert_thresholds_seven_day || []).join(', ');
    document.getElementById('thresholdCodex5h').value = (s.alert_thresholds_codex_five_hour || []).join(', ');
    document.getElementById('thresholdCodex7d').value = (s.alert_thresholds_codex_seven_day || []).join(', ');
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
    renderProviders(state.status.providers || []);
    renderDiagnostics(state.status);
    drawUsageChart(document.getElementById('usageChart'), state.history.rows || []);
    drawBurnChart(document.getElementById('burnChart'), state.history.rows || []);
    renderPredictions(state.status);
    renderHeatmap(state.history.rows || [], state.status.settings || {});
    document.getElementById('historyMeta').textContent = `${state.history.rows.length} rows · ${state.range}`;
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
            <div class="bar"><div class="fill ${pct >= 100 ? 'warn' : ''}" style="width:${Math.min(100, Math.max(0, pct))}%"></div></div>
            <p class="muted">${escapeHtml(metricSubtext(entry))}</p>
        `;
        list.appendChild(item);
    }
    if (!provider.usage.length && !provider.error) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = 'Waiting for usage data';
        list.appendChild(empty);
    }
    card.appendChild(list);
    return card;
}

function metricSubtext(entry) {
    const parts = [entry.reset_text || 'No reset time'];
    if (entry.burn) {
        const pace = entry.burn.healthy ? 'on pace' : 'ahead of pace';
        if (entry.burn.eta_seconds) parts.push(`ETA ${formatCountdown(entry.burn.eta_seconds)}`);
        parts.push(`${Math.round(entry.burn.burn_per_hour * 10) / 10} pp/h`);
        parts.push(pace);
    }
    return parts.join(' · ');
}

function renderDiagnostics(status) {
    const root = document.getElementById('diagnostics');
    const cards = [
        ['App', `${status.app.name} ${status.app.version}`],
        ['Dashboard bind', status.privacy.bind],
        ['Analytics', status.privacy.analytics ? 'enabled' : 'disabled'],
        ['Token payloads', status.privacy.token_free ? 'not exposed' : 'check configuration'],
        ['Next update', status.next_poll_time ? formatCountdown(status.next_poll_time - Date.now() / 1000) : 'unknown'],
    ];
    for (const provider of status.providers || []) {
        const versions = (provider.installations || []).map(i => `${i.name} ${i.version}`).join(', ') || 'not detected';
        cards.push([`${provider.label} CLI`, versions]);
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
    document.getElementById('burnMeta').textContent = 'percentage points per hour';
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
                day: `${Math.round(dayPct)}% by ${target}`,
                period: `${Math.round(periodPct)}% by reset`,
                tone: dayPct >= 100 || periodPct >= 100 ? 'warn' : 'ok',
            });
        }
    }

    document.getElementById('predictionMeta').textContent = `local day target ${target}`;
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
        empty.textContent = 'Waiting for enough usage data';
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
    document.getElementById('heatmapMeta').textContent = 'positive usage deltas by local hour';
    root.replaceChildren(...(nodes.length ? nodes : [emptyMuted('Waiting for history data')]));
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
    ctx.strokeStyle = colors.grid;
    ctx.fillStyle = colors.dim;
    ctx.font = '12px Segoe UI, sans-serif';

    for (let i = 0; i <= 4; i++) {
        const y = pad.t + (h - pad.t - pad.b) * i / 4;
        ctx.beginPath();
        ctx.moveTo(pad.l, y);
        ctx.lineTo(w - pad.r, y);
        ctx.stroke();
    }

    if (!points.length) {
        ctx.fillText('Waiting for history data', pad.l, h / 2);
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
    if (key.includes('codex')) return key.includes('seven') ? '#0f766e' : colors.codex;
    if (key.includes('seven')) return colors.seven;
    return [colors.claude, '#f59e0b', '#db2777', '#475569'][index % 4];
}

function map(value, inMin, inMax, outMin, outMax) {
    if (inMax === inMin) return (outMin + outMax) / 2;
    return outMin + (value - inMin) * (outMax - outMin) / (inMax - inMin);
}

function formatUpdated(ts) {
    if (!ts) return 'waiting';
    return `${formatCountdown(Date.now() / 1000 - ts)} ago`;
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
        codex_enabled: document.getElementById('codexEnabled').checked,
        tooltip_fields: parseList(document.getElementById('tooltipFields').value),
        alert_thresholds_five_hour: parseNumbers(document.getElementById('thresholdClaude5h').value),
        alert_thresholds_seven_day: parseNumbers(document.getElementById('thresholdClaude7d').value),
        alert_thresholds_codex_five_hour: parseNumbers(document.getElementById('thresholdCodex5h').value),
        alert_thresholds_codex_seven_day: parseNumbers(document.getElementById('thresholdCodex7d').value),
        prediction_enabled: document.getElementById('predictionEnabled').checked,
        prediction_day_end_time: document.getElementById('predictionDayEnd').value || '18:00',
        heatmap_enabled: document.getElementById('heatmapEnabled').checked,
        quiet_hours_enabled: document.getElementById('quietHoursEnabled').checked,
        quiet_hours_start: document.getElementById('quietHoursStart').value || '22:00',
        quiet_hours_end: document.getElementById('quietHoursEnd').value || '08:00',
        on_reset_command: document.getElementById('resetCommand').value.trim(),
        on_threshold_command: document.getElementById('thresholdCommand').value.trim(),
    };
    const result = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(r => r.json());
    document.getElementById('settingsStatus').textContent = result.ok
        ? `saved to ${result.path}; restart required`
        : `error: ${(result.errors || []).join(', ')}`;
});

document.getElementById('testReset').addEventListener('click', () => testEvent('reset'));
document.getElementById('testThreshold').addEventListener('click', () => testEvent('threshold'));

async function testEvent(event) {
    const result = await fetch('/api/test-event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event }),
    }).then(r => r.json());
    document.getElementById('settingsStatus').textContent = result.ok ? `test ${event} fired` : `test failed`;
}

refresh();
loadSettings();
setInterval(refresh, 15000);
