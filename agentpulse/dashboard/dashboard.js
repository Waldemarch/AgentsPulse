const RANGE_SECONDS = { '24h': 24 * 3600, '7d': 7 * 24 * 3600, '30d': 30 * 24 * 3600 };
// Readings whose reset times differ by less than this belong to one quota
// cycle: the APIs repeat the same reset with a few seconds of jitter.
const RESET_TOLERANCE_SECONDS = 10 * 60;

function themeColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

// One colour per provider, defined in dashboard.css for both themes (a palette
// checked for colour-blind safety); other providers share a neutral colour.
function providerColor(provider) {
    return themeColor(`--series-${provider}`, '') || themeColor('--series-other', '#898781');
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

// historyKey identifies the loaded history (range plus the latest reading of
// every provider), so history is downloaded again only when it changed.
let state = { status: null, history: null, range: '24h', historyKey: '' };

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
    state.historyKey = '';
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

function historyKey(status) {
    const readings = (status.providers || []).map(provider => `${provider.id}:${provider.last_success_time || ''}`);
    return `${state.range}|${readings.join(',')}`;
}

async function refresh() {
    if (document.hidden) return;
    const range = state.range;
    try {
        const status = await fetchJson('/api/status');
        const key = historyKey(status);
        let loaded = state.history;
        if (key !== state.historyKey || !loaded) {
            loaded = await fetchJson(`/api/history?range=${encodeURIComponent(range)}`);
            // A range switch while this request ran has started its own refresh.
            if (state.range !== range) return;
            state.historyKey = key;
        }
        state = { ...state, status, history: loaded };
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
    // One command per line: each runs on its own, exactly like the array in the settings file.
    document.getElementById('resetCommand').value = (s.on_reset_command || []).join('\n');
    document.getElementById('thresholdCommand').value = (s.on_threshold_command || []).join('\n');
}

function render() {
    if (!state.status || !state.history) return;
    const rows = state.history.rows || [];
    renderProviders(state.status.providers || []);
    renderDiagnostics(state.status);
    renderCharts();
    renderPredictions(state.status);
    renderHeatmap(rows, state.history.fields || {}, state.status);
    document.getElementById('historyMeta').textContent = fmt(tr('rows', '{count} rows · {range}'), { count: rows.length, range: state.history.range || state.range });
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
    const name = document.createElement('h2');
    name.textContent = provider.label;
    const updated = document.createElement('span');
    updated.textContent = formatUpdated(provider.last_success_time);
    title.append(name, updated);
    card.appendChild(title);

    if (provider.error) {
        const err = document.createElement('p');
        err.className = 'error';
        err.textContent = provider.error;
        card.appendChild(err);
    }

    const list = document.createElement('div');
    list.className = 'usage-list';
    for (const entry of provider.usage) list.appendChild(usageItem(entry));
    if (!provider.usage.length && !provider.error) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = tr('waiting_usage', 'Waiting for usage data');
        list.appendChild(empty);
    }
    card.appendChild(list);
    return card;
}

function usageItem(entry) {
    const pct = Math.round(entry.utilization);
    const item = document.createElement('div');

    const row = document.createElement('div');
    row.className = 'metric-row';
    const label = document.createElement('span');
    label.textContent = entry.label;
    const value = document.createElement('strong');
    value.textContent = `${pct}%`;
    row.append(label, value);

    const bar = document.createElement('div');
    bar.className = 'bar';
    const fill = document.createElement('div');
    fill.className = pct >= 100 ? 'fill warn' : pct >= 80 ? 'fill high' : 'fill';
    // Set through the CSSOM: the dashboard's Content-Security-Policy ignores
    // widths written into markup.
    fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    bar.appendChild(fill);

    const detail = document.createElement('p');
    detail.className = 'muted';
    detail.textContent = metricSubtext(entry);

    item.append(row, bar, detail);
    return item;
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

// ---------- Charts ----------

// Every quota series in the history rows, ordered like the provider cards.
// Field labels and window lengths come from the server, so new quota fields
// show up without any change here.
function quotaSeries(rows, fields, providers) {
    const byKey = new Map();
    for (const row of rows) {
        if (row.utilization === null || !row.field) continue;
        const key = `${row.provider}:${row.field}`;
        if (!byKey.has(key)) {
            const meta = fields[row.field] || {};
            byKey.set(key, {
                provider: row.provider,
                field: row.field,
                period: meta.period_seconds || null,
                variant: meta.variant || null,
                fieldLabel: meta.label || row.field,
                points: [],
            });
        }
        byKey.get(key).points.push({ ts: row.ts, value: row.utilization, reset: resetSeconds(row.resets_at) });
    }
    const order = providers.map(provider => provider.id);
    const rank = id => (order.includes(id) ? order.indexOf(id) : order.length);
    const series = Array.from(byKey.values());
    for (const entry of series) entry.points.sort((a, b) => a.ts - b.ts);
    return series.sort((a, b) => rank(a.provider) - rank(b.provider) || (a.variant ? 1 : 0) - (b.variant ? 1 : 0) || a.field.localeCompare(b.field));
}

function resetSeconds(value) {
    const ms = Date.parse(value || '');
    return Number.isFinite(ms) ? ms / 1000 : null;
}

function sameCycle(a, b) {
    if (a.reset === null || b.reset === null) return a.reset === b.reset;
    return Math.abs(a.reset - b.reset) <= RESET_TOLERANCE_SECONDS;
}

// Splits readings into runs of one quota cycle each, so a reset or the start
// of a new window never draws a line between two different cycles.
function cycleRuns(points) {
    const runs = [];
    for (const point of points) {
        const run = runs[runs.length - 1];
        if (run && sameCycle(run[run.length - 1], point)) run.push(point);
        else runs.push([point]);
    }
    return runs;
}

function providerLabel(id) {
    const provider = ((state.status && state.status.providers) || []).find(entry => entry.id === id);
    return provider ? provider.label : id;
}

function seriesLabel(entry) {
    return entry.variant ? `${providerLabel(entry.provider)} ${entry.fieldLabel}` : providerLabel(entry.provider);
}

function chartSpan() {
    const to = Date.now() / 1000;
    const span = RANGE_SECONDS[state.history.range] || RANGE_SECONDS['24h'];
    // Longer than this without a reading (app closed, workstation locked) the
    // last value is held as a step instead of a slope.
    const gapSeconds = Math.max(20 * 60, (state.history.bucket_seconds || 180) * 2.5);
    return { from: to - span, to, gapSeconds };
}

// One chart per quota window length (session, weekly, ...), so every field
// the API reports is shown without mixing time scales on one axis.
function periodGroups(series) {
    const groups = new Map();
    for (const entry of series) {
        const key = entry.period === null ? `field:${entry.field}` : entry.period;
        if (!groups.has(key)) groups.set(key, { period: entry.period, series: [] });
        groups.get(key).series.push(entry);
    }
    const sorted = Array.from(groups.values()).sort((a, b) => (a.period ?? Infinity) - (b.period ?? Infinity));
    for (const group of sorted) group.title = (group.series.find(entry => !entry.variant) || group.series[0]).fieldLabel;
    return sorted;
}

function lineFor(entry, runs) {
    return {
        color: providerColor(entry.provider),
        width: entry.variant ? 1.5 : 2,
        alpha: entry.variant ? 0.6 : 1,
        runs,
    };
}

function legend(series) {
    const list = document.createElement('div');
    list.className = 'legend';
    for (const entry of series) {
        const item = document.createElement('span');
        item.className = 'legend-item';
        const key = document.createElement('i');
        key.className = entry.variant ? 'legend-key variant' : 'legend-key';
        key.style.background = providerColor(entry.provider);
        const text = document.createElement('span');
        text.textContent = seriesLabel(entry);
        item.append(key, text);
        list.appendChild(item);
    }
    return list;
}

function renderCharts() {
    const series = quotaSeries(state.history.rows || [], state.history.fields || {}, state.status.providers || []);
    const span = chartSpan();
    renderUsageCharts(series, span);
    renderBurnChart(series, span);
}

function renderUsageCharts(series, span) {
    const root = document.getElementById('usageCharts');
    const groups = periodGroups(series);
    if (!groups.length) {
        root.replaceChildren(emptyMuted(tr('waiting_history', 'Waiting for history data')));
        return;
    }
    const blocks = groups.map(group => {
        const block = document.createElement('div');
        block.className = 'chart-block';
        const head = document.createElement('div');
        head.className = 'chart-head';
        const title = document.createElement('h3');
        title.textContent = group.title;
        head.append(title, legend(group.series));
        const canvas = document.createElement('canvas');
        canvas.className = 'chart-canvas';
        block.append(head, canvas);
        return { block, canvas, group };
    });
    root.replaceChildren(...blocks.map(entry => entry.block));
    for (const { canvas, group } of blocks) {
        const lines = group.series.map(entry => lineFor(entry, cycleRuns(entry.points)));
        drawLineChart(canvas, lines, { ...span, minY: 0, maxY: 100, step: 25, unit: '%' });
    }
}

// Burn rate of each provider's shortest base window (the session), where
// pace decides whether the quota lasts.  Rates are taken only between
// consecutive readings of one cycle, never across a reset or a gap.
function renderBurnChart(series, span) {
    const shortest = new Map();
    for (const entry of series) {
        if (entry.variant || entry.period === null) continue;
        const current = shortest.get(entry.provider);
        if (!current || entry.period < current.period) shortest.set(entry.provider, entry);
    }
    const chosen = Array.from(shortest.values());
    const lines = chosen.map(entry => lineFor(entry, burnRuns(entry.points, span.gapSeconds)));
    const labels = new Set(chosen.map(entry => entry.fieldLabel));
    const unit = tr('pp_per_hour', 'percentage points per hour');
    document.getElementById('burnMeta').textContent = labels.size === 1 ? `${Array.from(labels)[0]} · ${unit}` : unit;
    document.getElementById('burnLegend').replaceChildren(legend(chosen));
    drawLineChart(document.getElementById('burnChart'), lines, { ...span, gapSeconds: Infinity, minY: 0, maxY: 60, step: 20, unit: '' });
}

function burnRuns(points, gapSeconds) {
    const runs = [];
    for (const run of cycleRuns(points)) {
        let current = [];
        for (let index = 1; index < run.length; index++) {
            const previous = run[index - 1];
            const point = run[index];
            const seconds = point.ts - previous.ts;
            if (seconds <= 0 || seconds > gapSeconds) {
                if (current.length) runs.push(current);
                current = [];
                continue;
            }
            current.push({ ts: point.ts, value: (point.value - previous.value) / (seconds / 3600) });
        }
        if (current.length) runs.push(current);
    }
    return runs;
}

// Sizes the backing store from the canvas's CSS box on every draw, so repeated
// draws keep the same on-screen size at any display scaling.
function prepareCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
}

// Draws lines on a fixed time window with value and time axes.  Lines are
// clipped to the plot; within a run, a stretch without readings is drawn as
// a step (the last value held until the next reading).
function drawLineChart(canvas, lines, options) {
    const { ctx, width, height } = prepareCanvas(canvas);
    const plot = { x: 48, y: 10, w: Math.max(1, width - 60), h: Math.max(1, height - 34) };
    const x = ts => plot.x + ((ts - options.from) / (options.to - options.from)) * plot.w;
    const y = value => plot.y + plot.h - ((value - options.minY) / (options.maxY - options.minY)) * plot.h;
    const text = themeColor('--chart-text', '#667085');

    ctx.font = '11px "Segoe UI Variable", "Segoe UI", system-ui, sans-serif';
    ctx.lineWidth = 1;
    ctx.strokeStyle = themeColor('--chart-grid', '#d9dee7');
    ctx.fillStyle = text;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let value = options.minY; value <= options.maxY; value += options.step) {
        const lineY = Math.round(y(value)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(plot.x, lineY);
        ctx.lineTo(plot.x + plot.w, lineY);
        ctx.stroke();
        ctx.fillText(`${value}${options.unit}`, plot.x - 8, lineY);
    }

    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const tick of timeTicks(options.from, options.to, plot.w)) {
        const tickX = Math.round(x(tick.ts)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(tickX, plot.y);
        ctx.lineTo(tickX, plot.y + plot.h);
        ctx.stroke();
        if (tickX > plot.x + 18 && tickX < plot.x + plot.w - 18) ctx.fillText(tick.label, tickX, plot.y + plot.h + 7);
    }

    if (!lines.some(line => line.runs.length)) {
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(tr('waiting_history', 'Waiting for history data'), plot.x + 8, plot.y + plot.h / 2);
        return;
    }

    ctx.save();
    ctx.beginPath();
    ctx.rect(plot.x, plot.y, plot.w, plot.h);
    ctx.clip();
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    for (const line of lines) {
        ctx.strokeStyle = line.color;
        ctx.fillStyle = line.color;
        ctx.globalAlpha = line.alpha;
        ctx.lineWidth = line.width;
        for (const run of line.runs) {
            if (run.length === 1) {
                ctx.beginPath();
                ctx.arc(x(run[0].ts), y(run[0].value), line.width, 0, 2 * Math.PI);
                ctx.fill();
                continue;
            }
            ctx.beginPath();
            run.forEach((point, index) => {
                if (index === 0) {
                    ctx.moveTo(x(point.ts), y(point.value));
                    return;
                }
                const previous = run[index - 1];
                if (point.ts - previous.ts > options.gapSeconds) ctx.lineTo(x(point.ts), y(previous.value));
                ctx.lineTo(x(point.ts), y(point.value));
            });
            ctx.stroke();
        }
    }
    ctx.restore();
}

// Local-time axis ticks: every few hours for a day, daily for a week, weekly
// for a month.
function timeTicks(from, to, plotWidth) {
    const ticks = [];
    const tick = new Date(from * 1000);
    if (to - from <= 36 * 3600) {
        const stepHours = plotWidth < 480 ? 6 : 3;
        tick.setMinutes(0, 0, 0);
        tick.setHours(Math.ceil(tick.getHours() / stepHours) * stepHours);
        while (tick.getTime() / 1000 <= to) {
            ticks.push({ ts: tick.getTime() / 1000, label: tick.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) });
            tick.setHours(tick.getHours() + stepHours);
        }
        return ticks;
    }
    const stepDays = to - from <= 8 * 24 * 3600 ? 1 : 7;
    const format = stepDays === 1 ? { weekday: 'short', day: 'numeric' } : { day: 'numeric', month: 'short' };
    tick.setHours(24, 0, 0, 0);
    while (tick.getTime() / 1000 <= to) {
        ticks.push({ ts: tick.getTime() / 1000, label: tick.toLocaleDateString([], format) });
        tick.setDate(tick.getDate() + stepDays);
    }
    return ticks;
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
            const resetHours = secondsUntilIso(entry.resets_at) / 3600;
            // Usage never exceeds the quota, and a window that resets before the
            // day's end has no usage left to project past its reset.
            const periodPct = Math.min(100, entry.utilization + entry.burn.burn_per_hour * resetHours);
            const resetsFirst = resetHours > 0 && resetHours <= hoursToDayEnd;
            const dayPct = Math.min(100, entry.utilization + entry.burn.burn_per_hour * hoursToDayEnd);
            const byReset = fmt(tr('by_reset', '{pct}% by reset'), { pct: Math.round(periodPct) });
            cards.push({
                title: `${provider.label} ${entry.label}`,
                day: resetsFirst ? byReset : fmt(tr('by_time', '{pct}% by {time}'), { pct: Math.round(dayPct), time: target }),
                period: resetsFirst ? '' : byReset,
                trend: trendText(entry.trend),
                tone: periodPct >= 100 || (!resetsFirst && dayPct >= 100) ? 'warn' : 'ok',
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
            ${card.period ? `<span>${escapeHtml(card.period)}</span>` : ''}
            ${card.trend ? `<span class="trend">${escapeHtml(card.trend)}</span>` : ''}
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

// One series per provider - its longest base quota window - so the same work
// is not counted once for every quota field that it also consumed.
function heatmapSeries(series) {
    const chosen = new Map();
    for (const entry of series) {
        if (entry.variant || entry.period === null) continue;
        const current = chosen.get(entry.provider);
        if (!current || entry.period > current.period) chosen.set(entry.provider, entry);
    }
    return Array.from(chosen.values());
}

function renderHeatmap(rows, fields, status) {
    const settings = status.settings || {};
    const section = document.getElementById('heatmapSection');
    section.hidden = settings.heatmap_enabled === false;
    if (section.hidden) return;

    const root = document.getElementById('heatmap');
    const nodes = [];
    for (const entry of heatmapSeries(quotaSeries(rows, fields, status.providers || []))) {
        const hours = new Array(24).fill(0);
        for (const run of cycleRuns(entry.points)) {
            for (let index = 1; index < run.length; index++) {
                const delta = run[index].value - run[index - 1].value;
                if (delta > 0) hours[new Date(run[index].ts * 1000).getHours()] += delta;
            }
        }
        // Each provider on its own scale: the rows show when it is used, and
        // the tooltips carry the amounts.
        let max = 1;
        for (const value of hours) max = Math.max(max, value);
        const name = providerLabel(entry.provider);
        const label = document.createElement('div');
        label.className = 'heatmap-label';
        label.textContent = name;
        nodes.push(label);
        hours.forEach((value, hour) => {
            const cell = document.createElement('div');
            cell.className = 'heatmap-cell';
            cell.title = `${name} ${String(hour).padStart(2, '0')}:00 · ${Math.round(value * 10) / 10} pp`;
            cell.style.background = providerColor(entry.provider);
            cell.style.opacity = String(0.14 + 0.86 * value / max);
            cell.textContent = hour % 6 === 0 ? String(hour) : '';
            nodes.push(cell);
        });
    }
    document.getElementById('heatmapMeta').textContent = tr('heatmap_meta', 'positive usage deltas by local hour');
    root.replaceChildren(...(nodes.length ? nodes : [emptyMuted(tr('waiting_history', 'Waiting for history data'))]));
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

// Compares the current quota cycle's pace to past cycles at the same age -
// the one signal the single-cycle burn rate above can't give: whether this
// cycle is unusually heavy, not just whether it's on pace to run out.
function trendText(trend) {
    if (!trend || !Number.isFinite(trend.delta_pct) || !trend.cycles_compared) return null;
    const delta = Math.round(trend.delta_pct);
    const signed = `${delta > 0 ? '+' : ''}${delta}pp`;
    return fmt(tr('vs_usual_pace', '{delta} vs usual pace ({n} cycles)'), { delta: signed, n: trend.cycles_compared });
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

function parseLines(value) {
    return value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
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
        on_reset_command: parseLines(document.getElementById('resetCommand').value),
        on_threshold_command: parseLines(document.getElementById('thresholdCommand').value),
    };
    const result = await postJson('/api/settings', payload);
    const saved = result.restart_required
        ? tr('restart_required', 'settings saved - restart the app to apply the Codex or Kimi monitoring change')
        : tr('saved', 'settings saved');
    document.getElementById('settingsStatus').textContent = result.ok
        ? saved
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

let resizeFrame = 0;
window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
        if (state.status && state.history) renderCharts();
    });
});

// Nothing is fetched while the tab is in the background; coming back
// refreshes immediately instead of waiting for the next interval.
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
});

loadI18n().then(() => {
    refresh();
    loadSettings();
});
setInterval(refresh, 15000);
