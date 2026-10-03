const RANGE_SECONDS = { '24h': 24 * 3600, '7d': 7 * 24 * 3600, '30d': 30 * 24 * 3600 };
// Readings whose reset times differ by less than this belong to one quota
// cycle: the APIs repeat the same reset with a few seconds of jitter.
const RESET_TOLERANCE_SECONDS = 10 * 60;
const SVG_NS = 'http://www.w3.org/2000/svg';
const SEVERITY = { ok: 0, tight: 1, limit: 2, blocked: 3 };
const HOUR = 3600;
const DAY = 24 * HOUR;

// ---------- DOM helpers ----------

// Builds an element from attributes and children.  Colours and sizes are set
// through the CSSOM by the callers: the Content-Security-Policy ignores
// style attributes written into markup.
function build(namespace, tag, attrs, children) {
    const node = namespace ? document.createElementNS(namespace, tag) : document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'text') node.textContent = String(value);
        else if (key === 'class') node.setAttribute('class', value);
        else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

const h = (tag, attrs, ...children) => build(null, tag, attrs, children);
const s = (tag, attrs, ...children) => build(SVG_NS, tag, attrs, children);
const byId = (id) => document.getElementById(id);

function themeColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

// One colour per provider, defined in dashboard.css for both themes (a palette
// checked for colour-blind safety); other providers share a neutral colour.
function providerColor(provider) {
    return themeColor(`--series-${provider}`, '') || themeColor('--series-other', '#898781');
}

function readStored(key, fallback) {
    try {
        const value = localStorage.getItem(key);
        return value === null ? fallback : JSON.parse(value);
    } catch {
        return fallback;
    }
}

function writeStored(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // Storage can be unavailable (private windows); the choice then lasts for this page only.
    }
}

// ---------- Session token and requests ----------

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

async function fetchJson(path) {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        error.status = response.status;
        throw error;
    }
    return response.json();
}

// ---------- Translations ----------

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
        t = await fetch('/api/i18n', { cache: 'no-store' }).then((response) => response.json());
    } catch {
        t = {};
    }
    applyI18n();
}

function applyI18n() {
    for (const node of document.querySelectorAll('[data-i18n]')) {
        const value = t[node.dataset.i18n];
        if (value !== undefined) node.textContent = value;
    }
    for (const node of document.querySelectorAll('[data-i18n-label]')) {
        const value = t[node.dataset.i18nLabel];
        if (value !== undefined) node.setAttribute('aria-label', value);
    }
}

// ---------- Formatting ----------

const pct = (value) => `${Math.round(value)}%`;
const pad = (value) => String(value).padStart(2, '0');

function decimal(value, digits = 1) {
    return Number(value).toLocaleString([], { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// Monday-first weekday names from the app's language, so the dashboard reads
// like the tray even when the browser uses another language.
function weekdayName(date) {
    const index = (date.getDay() + 6) % 7;
    return tr(`weekday_${index}`, date.toLocaleDateString([], { weekday: 'short' }));
}

function clock(ts) {
    const date = new Date(ts * 1000);
    return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function startOfDay(ts) {
    const date = new Date(ts * 1000);
    date.setHours(0, 0, 0, 0);
    return date.getTime() / 1000;
}

function whenText(ts, now = Date.now() / 1000) {
    const days = Math.round((startOfDay(ts) - startOfDay(now)) / DAY);
    if (days === 0) return clock(ts);
    if (days === 1) return fmt(tr('clock_tomorrow', 'tomorrow {clock}'), { clock: clock(ts) });
    return fmt(tr('clock_weekday', '{day} {clock}'), { day: weekdayName(new Date(ts * 1000)), clock: clock(ts) });
}

function durationText(seconds) {
    const minutes = Math.max(1, Math.round(seconds / 60));
    if (minutes < 60) return fmt(tr('duration_m', '{m}m'), { m: minutes });
    const hours = Math.floor(minutes / 60);
    if (hours >= 48) return fmt(tr('duration_dh', '{d}d {h}h'), { d: Math.floor(hours / 24), h: hours % 24 });
    return fmt(tr('duration_hm', '{h}h {m}m'), { h: hours, m: minutes % 60 });
}

function countdownText(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    return `${Math.floor(seconds / 60)}:${pad(seconds % 60)}`;
}

function formatUpdated(ts) {
    if (!ts) return tr('waiting', 'waiting');
    return fmt(tr('ago', '{duration} ago'), { duration: durationText(Date.now() / 1000 - ts) });
}

// ---------- State ----------

// historyKey identifies the loaded history (range plus the latest reading of
// every provider), so history is downloaded again only when it changed.
const state = {
    status: null,
    history: null,
    range: readStored('agentpulse-range', '24h'),
    historyKey: '',
    hidden: new Set(readStored('agentpulse-hidden-series', [])),
    heatProvider: readStored('agentpulse-heat-provider', null),
    typicalProvider: readStored('agentpulse-typical-provider', null),
};
if (!RANGE_SECONDS[state.range]) state.range = '24h';

function providers() {
    return (state.status && state.status.providers) || [];
}

function providerLabel(id) {
    const provider = providers().find((entry) => entry.id === id);
    return provider ? provider.label : id;
}

function providerRank(id) {
    const index = providers().findIndex((entry) => entry.id === id);
    return index < 0 ? providers().length : index;
}

// ---------- Refresh ----------

function setConnectionError(error) {
    const banner = byId('connectionError');
    if (!error) {
        banner.hidden = true;
        return;
    }
    const expired = error.status === 403;
    banner.textContent = expired
        ? tr('session_expired', 'session expired - reopen the dashboard from the tray menu')
        : tr('connection_lost', 'Connection lost - retrying');
    banner.hidden = false;
}

function historyKey(status) {
    const readings = (status.providers || []).map((provider) => `${provider.id}:${provider.last_success_time || ''}`);
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
        state.status = status;
        state.history = loaded;
        setConnectionError(null);
        render();
    } catch (error) {
        setConnectionError(error);
    }
}

function render() {
    if (!state.status || !state.history) return;
    safely(renderSummary, 'summary');
    safely(renderNow, 'nowGrid');
    renderCharts();
    safely(renderHeatmap, 'heatmapGrid');
    safely(renderFooter, 'siteFooter');
    tickLive();
}

function renderCharts() {
    safely(renderLegend, 'historyLegend');
    safely(renderHistory, 'historyChart');
    safely(renderTypicalWeek, 'typicalChart');
    safely(renderSessions, 'sessionsChart');
    safely(renderConsumption, 'consumptionChart');
    if (byId('historyTable').open) safely(renderHistoryTable, 'historyTableBody');
}

// One failing widget must not blank the whole dashboard.
function safely(renderer, containerId) {
    try {
        renderer();
    } catch (error) {
        console.error(error);
        byId(containerId).replaceChildren(h('p', { class: 'muted', text: tr('render_failed', 'This part could not be drawn.') }));
    }
}

// The header counts down to the app's next reading.
function tickLive() {
    const node = byId('liveStatus');
    if (!state.status) return;
    const next = state.status.next_poll_time;
    const refreshing = providers().some((provider) => provider.refreshing);
    const parts = [tr('live', 'Live')];
    if (refreshing) parts.push(tr('status_refreshing', 'Refreshing...'));
    else if (next && next > Date.now() / 1000) {
        parts.push(fmt(tr('next_reading', 'next reading in {duration}'), { duration: countdownText(next - Date.now() / 1000) }));
    }
    node.textContent = parts.join(' · ');
    node.classList.add('on');
}

// ---------- Status of a quota ----------

function outlookOf(entry) {
    return entry.outlook || { status: entry.utilization >= 100 ? 'blocked' : 'ok', forecast_pct: null, limit_at: null, reset_at: null, day_end_pct: null };
}

function statusText(outlook) {
    if (outlook.status === 'blocked') return tr('status_blocked', 'Limit reached');
    if (outlook.status === 'limit') {
        if (!outlook.limit_at) return tr('status_limit', 'Limit before reset');
        return fmt(tr('status_limit_at', 'Limit ~{clock}'), { clock: whenText(outlook.limit_at) });
    }
    if (outlook.status === 'tight') return tr('status_tight', 'Tight');
    return tr('status_ok', 'On track');
}

// A limit falls between its band's busy end and its light end, or the reset
// when the light end lasts that long.  Both ends round to the nearest minute
// like the reset times, so an end at the reset matches them.  A session's band
// names the time only; a band from past cycles names the day too.
function limitBand(outlook) {
    if (!outlook.limit_earliest) return null;
    const name = (ts) => {
        const minute = Math.round(ts / 60) * 60;
        return outlook.method === 'pace' ? clock(minute) : whenText(minute);
    };
    const earliest = name(outlook.limit_earliest);
    const latest = name(outlook.limit_latest || outlook.reset_at);
    return earliest === latest ? null : { earliest, latest };
}

function hasBand(outlook) {
    return outlook.forecast_low_pct !== null && outlook.forecast_low_pct !== undefined
        && outlook.forecast_high_pct !== null && outlook.forecast_high_pct !== undefined;
}

// Whether the projection at the reset has a band wider than a point.
function resetBand(outlook) {
    return hasBand(outlook) && Math.round(outlook.forecast_low_pct) !== Math.round(outlook.forecast_high_pct);
}

// Whether the card names a band: the limit's, or the forecast's at the reset.
function bandShown(outlook) {
    if (outlook.status === 'limit') return !!(outlook.limit_at && limitBand(outlook));
    return (outlook.status === 'ok' || outlook.status === 'tight') && resetBand(outlook);
}

// The projection at the reset, with its band when that spans more than a point.
function forecastText(outlook) {
    const forecast = Math.round(outlook.forecast_pct);
    if (!resetBand(outlook)) return fmt(tr('forecast_at_reset', '~{pct}% at reset'), { pct: forecast });
    return fmt(tr('forecast_at_reset_band', '~{pct}% at reset ({low}-{high}%)'), {
        pct: forecast, low: Math.round(outlook.forecast_low_pct), high: Math.round(outlook.forecast_high_pct),
    });
}

function methodNote(outlook) {
    if (outlook.method === 'history') {
        const note = fmt(tr('forecast_from_history', 'Projected from your last {n} cycles'), { n: outlook.cycles });
        if (!bandShown(outlook)) return note;
        return `${note}\n${tr('forecast_band_history', 'Range: from your lightest to your busiest past cycle')}`;
    }
    if (outlook.method === 'average') return tr('forecast_from_average', 'Projected from the average pace so far');
    if (outlook.method === 'pace') {
        const note = tr('forecast_from_pace', 'Projected from the current pace');
        if (!bandShown(outlook)) return note;
        return `${note}\n${tr('forecast_band', 'Range: from your calmer pace (last 30 min or the session average) to your fastest quarter-hour')}`;
    }
    return '';
}

function statusIcon(status) {
    const icon = s('svg', { viewBox: '0 0 16 16', width: 13, height: 13, 'aria-hidden': 'true', class: 'status-icon' });
    if (status === 'ok') {
        icon.append(
            s('circle', { cx: 8, cy: 8, r: 6.6, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6 }),
            s('path', {
                d: 'M5 8.3l2.1 2.1 4-4.4', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
            }),
        );
    } else if (status === 'blocked') {
        icon.append(
            s('rect', { x: 3.5, y: 7, width: 9, height: 7, rx: 1.5, fill: 'currentColor' }),
            s('path', { d: 'M5.5 7V5.3a2.5 2.5 0 015 0V7', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6 }),
        );
    } else {
        icon.append(
            s('path', { d: 'M8 1.9l6.5 11.5h-13z', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linejoin': 'round' }),
            s('path', { d: 'M8 6.3v3.5', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.7, 'stroke-linecap': 'round' }),
            s('circle', { cx: 8, cy: 11.7, r: 0.95, fill: 'currentColor' }),
        );
    }
    return icon;
}

function statusChip(outlook) {
    return h('span', { class: `chip chip-${outlook.status}`, title: methodNote(outlook) || null }, statusIcon(outlook.status), statusText(outlook));
}

// Same order as the app's tooltip: the most severe status; a reached limit
// that lasts longest; otherwise the limit or reset that comes first.
function urgency(outlook) {
    if (outlook.status === 'blocked') return [SEVERITY.blocked, outlook.reset_at || 0];
    const moment = outlook.limit_at || outlook.reset_at || Infinity;
    return [SEVERITY[outlook.status] || 0, -moment];
}

function moreUrgent(a, b) {
    const [severityA, momentA] = urgency(a.outlook);
    const [severityB, momentB] = urgency(b.outlook);
    return severityA !== severityB ? severityA > severityB : momentA > momentB;
}

// ---------- Summary ----------

function sentenceFor(provider) {
    const name = provider.label;
    if (provider.error && !provider.usage.length) return fmt(tr('summary_error', '{provider}: the last update failed.'), { provider: name });
    let worst = null;
    for (const entry of provider.usage) {
        const candidate = { entry, outlook: outlookOf(entry) };
        if (!worst || moreUrgent(candidate, worst)) worst = candidate;
    }
    if (!worst) return fmt(tr('summary_waiting', '{provider}: waiting for usage data.'), { provider: name });
    const { entry, outlook } = worst;
    const vars = { provider: name, label: entry.label };
    if (outlook.status === 'blocked') {
        const when = outlook.reset_at ? whenText(outlook.reset_at) : '-';
        return fmt(tr('summary_blocked', '{provider}: {label} limit reached - available again {when}.'), { ...vars, when });
    }
    if (outlook.status === 'limit' && outlook.limit_at) {
        return fmt(tr('summary_limit_at', '{provider}: {label} runs out around {clock}, {lead} before its reset.'), {
            ...vars, clock: whenText(outlook.limit_at), lead: durationText(outlook.reset_at - outlook.limit_at),
        });
    }
    if (outlook.status === 'limit') {
        return fmt(tr('summary_limit', '{provider}: {label} may run out before its reset ({when}).'), { ...vars, when: whenText(outlook.reset_at) });
    }
    if (outlook.status === 'tight') {
        return fmt(tr('summary_tight', '{provider}: {label} is tight - about {pct} by its reset ({when}).'), {
            ...vars, pct: pct(outlook.forecast_pct), when: whenText(outlook.reset_at),
        });
    }
    return fmt(tr('summary_ok', '{provider}: every quota is on track.'), { provider: name });
}

function renderSummary() {
    const nodes = [];
    const ordered = [...providers()].sort((a, b) => worstSeverity(b) - worstSeverity(a) || providerRank(a.id) - providerRank(b.id));
    for (const provider of ordered) {
        nodes.push(h('span', { class: 'summary-item' }, dot(provider.id), sentenceFor(provider)));
    }
    byId('summary').replaceChildren(...nodes);
}

function worstSeverity(provider) {
    let worst = 0;
    for (const entry of provider.usage) worst = Math.max(worst, SEVERITY[outlookOf(entry).status] || 0);
    return worst;
}

function dot(provider) {
    const node = h('span', { class: 'dot', 'aria-hidden': 'true' });
    node.style.background = providerColor(provider);
    return node;
}

// ---------- "Now" cards ----------

function renderNow() {
    byId('nowGrid').replaceChildren(...providers().map(nowCard));
}

function nowCard(provider) {
    const head = h('header', { class: 'now-head' },
        dot(provider.id),
        h('h2', { text: provider.label }),
        h('span', { class: 'fresh', text: formatUpdated(provider.last_success_time) }));
    const body = [];
    if (provider.error) body.push(h('p', { class: 'error', text: provider.error }));
    for (const entry of provider.usage) body.push(quotaRow(entry));
    if (!provider.usage.length && !provider.error) body.push(h('p', { class: 'muted', text: tr('waiting_usage', 'Waiting for usage data') }));
    return h('article', { class: 'now-card' }, head, ...body);
}

function quotaRow(entry) {
    const outlook = outlookOf(entry);
    const top = h('div', { class: 'quota-top' },
        h('span', { class: 'quota-label', text: entry.label }),
        statusChip(outlook),
        h('strong', { class: 'quota-value', text: pct(entry.utilization) }));
    return h('div', { class: 'quota' }, top, meter(entry, outlook), h('p', { class: 'quota-sub', text: quotaDetail(entry, outlook) }));
}

function quotaDetail(entry, outlook) {
    const parts = [entry.reset_text || tr('no_reset', 'No reset time')];
    const forecast = outlook.forecast_pct;
    // A projected limit is named by the chip; calm and tight windows add where they are heading.
    if (outlook.status === 'limit' && outlook.limit_at && outlook.reset_at) {
        const band = limitBand(outlook);
        if (band) parts.push(fmt(tr('limit_between', 'limit between {earliest} and {latest}'), band));
        parts.push(fmt(tr('gap_before_reset', '~{duration} without quota before the reset'), { duration: durationText(outlook.reset_at - outlook.limit_at) }));
    } else if ((outlook.status === 'ok' || outlook.status === 'tight') && forecast !== null && forecast !== undefined && forecast - entry.utilization >= 0.5) {
        parts.push(forecastText(outlook));
    }
    // After today's day end the projection targets tomorrow's, so the time is named relative to now.
    if (outlook.day_end_pct !== null && outlook.day_end_pct !== undefined && state.status.day_end) {
        const target = whenText(state.status.day_end, state.status.now);
        parts.push(fmt(tr('by_time', '{pct}% by {time}'), { pct: Math.round(outlook.day_end_pct), time: target }));
    }
    // What today may use of a weekly quota so it lasts until the reset.
    if (entry.budget) {
        const budget = { used: Math.round(entry.budget.used), allowance: Math.round(entry.budget.allowance) };
        parts.push(fmt(tr('budget_today', 'today {used} of {allowance} pp'), budget));
    }
    const trend = trendText(entry.trend);
    if (trend) parts.push(trend);
    return parts.join(' · ');
}

// Compares the current quota cycle's pace to past cycles at the same age:
// whether this cycle is unusually heavy, not just whether it runs out.
function trendText(trend) {
    if (!trend || !Number.isFinite(trend.delta_pct) || !trend.cycles_compared) return null;
    const delta = Math.round(trend.delta_pct);
    const signed = `${delta > 0 ? '+' : ''}${delta}pp`;
    return fmt(tr('vs_usual_pace', '{delta} vs usual pace ({n} cycles)'), { delta: signed, n: trend.cycles_compared });
}

// Three layers: usage, a lighter projection to the reset, and a marker for the
// share of the window that has passed.  Multi-day windows show midnights, and
// a session's band shows as stripes from the slow to the fast pace.
function meter(entry, outlook) {
    const used = Math.min(100, Math.max(0, entry.utilization));
    const forecast = outlook.forecast_pct === null || outlook.forecast_pct === undefined ? used : Math.min(100, outlook.forecast_pct);
    const elapsed = entry.outlook ? entry.outlook.elapsed_pct : null;
    const label = [fmt(tr('meter_used', '{pct} used'), { pct: pct(used) })];
    if (forecast > used) label.push(forecastText(outlook));
    const node = h('div', { class: `meter meter-${outlook.status}`, role: 'img', 'aria-label': label.join(', ') });
    for (const position of midnights(entry)) {
        const tick = h('span', { class: 'meter-tick' });
        tick.style.left = `${position * 100}%`;
        node.append(tick);
    }
    const fill = h('span', { class: 'meter-used' });
    fill.style.width = `${used}%`;
    node.append(fill);
    if (forecast - used > 0.5) {
        const ghost = h('span', { class: 'meter-ghost' });
        ghost.style.left = `${used}%`;
        ghost.style.width = `${forecast - used}%`;
        node.append(ghost);
    }
    if (outlook.status !== 'blocked' && hasBand(outlook) && outlook.forecast_high_pct - outlook.forecast_low_pct >= 1) {
        const band = h('span', { class: 'meter-band' });
        band.style.left = `${Math.min(100, outlook.forecast_low_pct)}%`;
        band.style.width = `${Math.min(100, outlook.forecast_high_pct) - Math.min(100, outlook.forecast_low_pct)}%`;
        node.append(band);
    }
    if (elapsed !== null && elapsed !== undefined) {
        const marker = h('span', { class: 'meter-now' });
        marker.style.left = `${elapsed}%`;
        node.append(marker);
    }
    return node;
}

function midnights(entry) {
    const reset = resetSeconds(entry.resets_at);
    const period = entry.period_seconds;
    if (!reset || !period || period <= DAY) return [];
    const start = reset - period;
    const positions = [];
    const marker = new Date(start * 1000);
    marker.setHours(24, 0, 0, 0);
    while (marker.getTime() / 1000 < reset) {
        positions.push((marker.getTime() / 1000 - start) / period);
        marker.setDate(marker.getDate() + 1);
    }
    return positions;
}

function resetSeconds(value) {
    const ms = Date.parse(value || '');
    return Number.isFinite(ms) ? ms / 1000 : null;
}

// ---------- History series ----------

// Every quota series in the history rows, ordered like the provider cards.
// Field labels and window lengths come from the server, so new quota fields
// show up without any change here.
function quotaSeries() {
    const rows = state.history.rows || [];
    const fields = state.history.fields || {};
    const byKey = new Map();
    for (const row of rows) {
        if (row.utilization === null || !row.field) continue;
        const key = `${row.provider}:${row.field}`;
        if (!byKey.has(key)) {
            const meta = fields[row.field] || {};
            byKey.set(key, {
                key,
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
    const series = Array.from(byKey.values());
    for (const entry of series) entry.points.sort((a, b) => a.ts - b.ts);
    return series.sort((a, b) => providerRank(a.provider) - providerRank(b.provider)
        || (a.variant ? 1 : 0) - (b.variant ? 1 : 0)
        || a.field.localeCompare(b.field));
}

function seriesName(entry) {
    return entry.variant ? `${providerLabel(entry.provider)} · ${entry.fieldLabel}` : providerLabel(entry.provider);
}

// Table columns name the window of base series too, since the table lists every window side by side.
function columnName(entry) {
    return entry.variant ? seriesName(entry) : `${providerLabel(entry.provider)} · ${entry.fieldLabel}`;
}

function isHidden(entry) {
    return state.hidden.has(entry.provider) || (entry.variant !== null && state.hidden.has(entry.key));
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

function chartSpan() {
    const to = Date.now() / 1000;
    const span = RANGE_SECONDS[state.history.range] || RANGE_SECONDS['24h'];
    // Longer than this without a reading (app closed, workstation locked) the
    // last value is held as a step instead of a slope.
    const gapSeconds = Math.max(20 * 60, (state.history.bucket_seconds || 180) * 2.5);
    return { from: to - span, to, gapSeconds };
}

// One panel per quota window length (session, weekly, ...), so every field
// the API reports is shown without mixing time scales on one axis.
function periodGroups(series) {
    const groups = new Map();
    for (const entry of series) {
        const key = entry.period === null ? `field:${entry.field}` : entry.period;
        if (!groups.has(key)) groups.set(key, { period: entry.period, series: [] });
        groups.get(key).series.push(entry);
    }
    const sorted = Array.from(groups.values()).sort((a, b) => (a.period ?? Infinity) - (b.period ?? Infinity));
    for (const group of sorted) group.title = (group.series.find((entry) => !entry.variant) || group.series[0]).fieldLabel;
    return sorted;
}

// Local-time axis ticks: every few hours for a day, daily for a week, weekly
// for a month.
function timeTicks(from, to, plotWidth) {
    const ticks = [];
    const tick = new Date(from * 1000);
    if (to - from <= 36 * HOUR) {
        const stepHours = plotWidth < 480 ? 6 : 3;
        tick.setMinutes(0, 0, 0);
        tick.setHours(Math.ceil(tick.getHours() / stepHours) * stepHours);
        while (tick.getTime() / 1000 <= to) {
            ticks.push({ ts: tick.getTime() / 1000, label: `${pad(tick.getHours())}:00` });
            tick.setHours(tick.getHours() + stepHours);
        }
        return ticks;
    }
    const stepDays = to - from <= 8 * DAY ? 1 : 7;
    tick.setHours(24, 0, 0, 0);
    if (stepDays === 7) {
        while (tick.getDay() !== 1) tick.setDate(tick.getDate() + 1);
    }
    while (tick.getTime() / 1000 <= to) {
        const date = new Date(tick);
        const label = stepDays === 1 ? `${weekdayName(date)} ${date.getDate()}` : date.toLocaleDateString([], { day: 'numeric', month: 'short' });
        ticks.push({ ts: tick.getTime() / 1000, label });
        tick.setDate(tick.getDate() + stepDays);
    }
    return ticks;
}

// Background bands: nights on a day's chart, weekends on longer ones.
function bands(from, to, nights = to - from <= 36 * HOUR) {
    const result = [];
    const day = new Date(from * 1000);
    day.setHours(0, 0, 0, 0);
    while (day.getTime() / 1000 < to) {
        const start = day.getTime() / 1000;
        const next = new Date(day);
        next.setDate(next.getDate() + 1);
        if (nights) {
            const morning = new Date(day);
            morning.setHours(6, 0, 0, 0);
            result.push([start, morning.getTime() / 1000]);
        } else if (day.getDay() === 0 || day.getDay() === 6) {
            result.push([start, next.getTime() / 1000]);
        }
        day.setDate(day.getDate() + 1);
    }
    return result.map(([a, b]) => [Math.max(from, a), Math.min(to, b)]).filter(([a, b]) => b > a);
}

function nearest(points, ts, limit) {
    if (!points.length) return null;
    let low = 0;
    let high = points.length - 1;
    while (low < high) {
        const middle = (low + high) >> 1;
        if (points[middle].ts < ts) low = middle + 1;
        else high = middle;
    }
    let best = points[low];
    if (low > 0 && Math.abs(points[low - 1].ts - ts) < Math.abs(best.ts - ts)) best = points[low - 1];
    return Math.abs(best.ts - ts) <= limit ? best : null;
}

// ---------- Legend ----------

function renderLegend() {
    const series = quotaSeries();
    const entries = [];
    const seen = new Set();
    for (const entry of series) {
        if (!seen.has(entry.provider)) {
            seen.add(entry.provider);
            entries.push({ key: entry.provider, provider: entry.provider, text: providerLabel(entry.provider), variant: false });
        }
    }
    for (const entry of series) {
        if (entry.variant) entries.push({ key: entry.key, provider: entry.provider, text: seriesName(entry), variant: true });
    }
    byId('historyLegend').replaceChildren(...entries.map((entry) => {
        const key = h('span', { class: entry.variant ? 'key variant' : 'key' });
        key.style.background = providerColor(entry.provider);
        return h('button', {
            type: 'button',
            'aria-pressed': state.hidden.has(entry.key) ? 'false' : 'true',
            onclick: () => toggleSeries(entry.key),
        }, key, entry.text);
    }));
}

function toggleSeries(key) {
    if (state.hidden.has(key)) state.hidden.delete(key);
    else state.hidden.add(key);
    writeStored('agentpulse-hidden-series', Array.from(state.hidden));
    renderCharts();
}

// ---------- Tooltip ----------

function showTip(content, clientX, clientY) {
    const tip = byId('tip');
    tip.replaceChildren(...[].concat(content));
    tip.hidden = false;
    const rect = tip.getBoundingClientRect();
    let left = clientX + 14;
    let top = clientY + 14;
    if (left + rect.width > window.innerWidth - 8) left = clientX - rect.width - 14;
    if (top + rect.height > window.innerHeight - 8) top = clientY - rect.height - 14;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
}

function hideTip() {
    byId('tip').hidden = true;
}

function tipRow(provider, value, name, variant = false) {
    const key = h('span', { class: variant ? 'key variant' : 'key' });
    key.style.background = providerColor(provider);
    return h('div', { class: 'tip-row' }, key, h('strong', { text: value }), h('span', { text: name }));
}

// ---------- History chart ----------

const HISTORY_MARGIN = { top: 26, bottom: 30, left: 44 };
const PANEL_GAP = 40;

function renderHistory() {
    const root = byId('historyChart');
    const series = quotaSeries();
    const span = chartSpan();
    const groups = periodGroups(series);
    const meta = state.history.bucket_seconds
        ? fmt(tr('points_bucketed', '{count} points · highest reading per {minutes} min'), {
            count: pointCount(series), minutes: state.history.bucket_seconds / 60,
        })
        : fmt(tr('points_raw', '{count} readings'), { count: pointCount(series) });
    byId('historyMeta').textContent = meta;
    if (!groups.length) {
        root.replaceChildren(h('p', { class: 'muted empty', text: tr('waiting_history', 'Waiting for history data') }));
        return;
    }

    const width = Math.max(300, Math.floor(root.clientWidth));
    const narrow = width < 640;
    const margin = { ...HISTORY_MARGIN, right: narrow ? 12 : 128 };
    const panelHeight = narrow ? 120 : 150;
    const height = margin.top + groups.length * panelHeight + (groups.length - 1) * PANEL_GAP + margin.bottom;
    const plotRight = width - margin.right;
    const x = (ts) => margin.left + ((ts - span.from) / (span.to - span.from)) * (plotRight - margin.left);
    const top = (index) => margin.top + index * (panelHeight + PANEL_GAP);
    const y = (index, value) => top(index) + (1 - Math.max(0, Math.min(100, value)) / 100) * panelHeight;
    const svg = s('svg', {
        class: 'chart', width, height, viewBox: `0 0 ${width} ${height}`, tabindex: 0, role: 'img',
        'aria-label': fmt(tr('history_label', 'Usage history, {range}'), { range: rangeName(state.history.range) }),
    });

    for (const [a, b] of bands(span.from, span.to)) {
        groups.forEach((group, index) => svg.append(s('rect', { class: 'band', x: x(a), y: top(index), width: x(b) - x(a), height: panelHeight })));
    }
    for (const tick of timeTicks(span.from, span.to, plotRight - margin.left)) {
        const tickX = x(tick.ts);
        groups.forEach((group, index) => svg.append(s('line', { class: 'grid-line', x1: tickX, x2: tickX, y1: top(index), y2: top(index) + panelHeight })));
        if (tickX - margin.left < 20 || plotRight - tickX < 20) continue;
        svg.append(s('text', { class: 'tick', x: tickX, y: height - 9, 'text-anchor': 'middle', text: tick.label }));
    }

    const visible = [];
    groups.forEach((group, index) => {
        svg.append(s('text', { class: 'panel-title', x: margin.left, y: top(index) - 9, text: group.title }));
        for (const value of [0, 50, 100]) {
            svg.append(s('line', { class: value === 0 ? 'axis-line' : 'grid-line', x1: margin.left, x2: plotRight, y1: y(index, value), y2: y(index, value) }));
            const text = value === 100 ? '100%' : String(value);
            svg.append(s('text', { class: 'tick', x: margin.left - 8, y: y(index, value) + 4, 'text-anchor': 'end', text }));
        }
        const clip = `clip-panel-${index}`;
        svg.append(s('clipPath', { id: clip }, s('rect', { x: margin.left, y: top(index) - 2, width: plotRight - margin.left, height: panelHeight + 4 })));
        const layer = s('g', { 'clip-path': `url(#${clip})` });
        for (const entry of group.series) {
            if (isHidden(entry)) continue;
            visible.push({ entry, index });
            drawSeries(layer, entry, (point) => x(point.ts), (point) => y(index, point.value), span.gapSeconds);
        }
        svg.append(layer);
        if (!narrow) endLabels(svg, group.series.filter((entry) => !isHidden(entry)), index, x, y, plotRight, span);
    });

    attachCrosshair(svg, { visible, groups, span, x, y, width, margin, height });
    root.replaceChildren(svg);
}

function pointCount(series) {
    let count = 0;
    for (const entry of series) count += entry.points.length;
    return count;
}

function rangeName(range) {
    return tr(`range_${range}`, range);
}

// A path per quota cycle: a stretch without readings holds the last value as
// a step, and a single reading shows as a dot.
function drawSeries(layer, entry, fx, fy, gapSeconds) {
    const color = providerColor(entry.provider);
    for (const run of cycleRuns(entry.points)) {
        if (run.length === 1) {
            layer.append(s('circle', { cx: fx(run[0]), cy: fy(run[0]), r: 2.2, fill: color, class: entry.variant ? 'variant' : null }));
            continue;
        }
        let path = '';
        run.forEach((point, index) => {
            if (index === 0) {
                path += `M${fx(point).toFixed(1)},${fy(point).toFixed(1)}`;
                return;
            }
            if (point.ts - run[index - 1].ts > gapSeconds) path += `H${fx(point).toFixed(1)}`;
            path += `L${fx(point).toFixed(1)},${fy(point).toFixed(1)}`;
        });
        layer.append(s('path', { d: path, stroke: color, class: entry.variant ? 'line variant' : 'line' }));
    }
}

// Latest value of each visible line at the right edge, nudged apart so labels never overlap.
function endLabels(svg, series, index, x, y, plotRight, span) {
    const labels = [];
    for (const entry of series) {
        const last = entry.points[entry.points.length - 1];
        if (!last || span.to - last.ts > span.gapSeconds * 2) continue;
        labels.push({ entry, value: last.value, x: x(last.ts), y: y(index, last.value), labelY: y(index, last.value) });
    }
    labels.sort((a, b) => a.labelY - b.labelY);
    labels.forEach((label, position) => {
        if (position) label.labelY = Math.max(label.labelY, labels[position - 1].labelY + 15);
    });
    const overflow = labels.length ? labels[labels.length - 1].labelY - (y(index, 0) + 4) : 0;
    if (overflow > 0) for (const label of labels) label.labelY -= overflow;
    for (const label of labels) {
        const labelX = plotRight + 12;
        svg.append(s('line', { class: 'leader', x1: label.x + 3, y1: label.y, x2: labelX - 3, y2: label.labelY }));
        svg.append(s('text', { class: 'end-label', x: labelX, y: label.labelY + 4 },
            s('tspan', { class: 'end-value', text: pct(label.value) }),
            s('tspan', { dx: 5, text: label.entry.variant ? label.entry.fieldLabel : providerLabel(label.entry.provider) })));
    }
}

// One vertical line and one tooltip for every visible series; the chart also
// takes the keyboard (arrows move, Home/End jump, Escape hides).
function attachCrosshair(svg, chart) {
    const { visible, groups, span, x, y, width, margin, height } = chart;
    const plotRight = width - margin.right;
    const cross = s('g', { class: 'crosshair', visibility: 'hidden' });
    const line = s('line', { class: 'cross-line', y1: margin.top - 4, y2: height - margin.bottom });
    cross.append(line);
    const dots = new Map();
    for (const { entry } of visible) {
        const marker = s('circle', { r: 4, class: 'cross-dot', fill: providerColor(entry.provider) });
        dots.set(entry.key, marker);
        cross.append(marker);
    }
    const hit = s('rect', { class: 'hit', x: margin.left, y: margin.top - 4, width: plotRight - margin.left, height: height - margin.top - margin.bottom + 4 });
    svg.append(cross, hit);

    let focusTs = span.to;
    const moveTo = (ts, clientX, clientY) => {
        const target = Math.max(span.from, Math.min(span.to, ts));
        let snapped = null;
        const readings = visible.map(({ entry, index }) => {
            const point = nearest(entry.points, target, span.gapSeconds);
            if (point && (snapped === null || Math.abs(point.ts - target) < Math.abs(snapped - target))) snapped = point.ts;
            return { entry, index, point };
        });
        const at = snapped === null ? target : snapped;
        focusTs = at;
        line.setAttribute('x1', x(at));
        line.setAttribute('x2', x(at));
        for (const { entry, index, point } of readings) {
            const marker = dots.get(entry.key);
            if (!point) {
                marker.setAttribute('visibility', 'hidden');
                continue;
            }
            marker.setAttribute('visibility', 'visible');
            marker.setAttribute('cx', x(point.ts));
            marker.setAttribute('cy', y(index, point.value));
        }
        cross.setAttribute('visibility', 'visible');
        const rows = [h('div', { class: 'tip-head', text: `${weekdayName(new Date(at * 1000))} ${new Date(at * 1000).getDate()}, ${clock(at)}` })];
        groups.forEach((group, index) => {
            const inPanel = readings.filter((reading) => reading.index === index);
            if (!inPanel.length) return;
            rows.push(h('div', { class: 'tip-group', text: group.title }));
            for (const { entry, point } of inPanel) rows.push(tipRow(entry.provider, point ? pct(point.value) : '-', seriesName(entry), !!entry.variant));
        });
        let px = clientX;
        let py = clientY;
        if (px === undefined) {
            const bounds = svg.getBoundingClientRect();
            px = bounds.left + x(at) * (bounds.width / width);
            py = bounds.top + margin.top + 24;
        }
        showTip(rows, px, py);
    };
    const hide = () => {
        cross.setAttribute('visibility', 'hidden');
        hideTip();
    };
    hit.addEventListener('pointermove', (event) => {
        const bounds = svg.getBoundingClientRect();
        const px = (event.clientX - bounds.left) * (width / bounds.width);
        moveTo(span.from + ((px - margin.left) / (plotRight - margin.left)) * (span.to - span.from), event.clientX, event.clientY);
    });
    hit.addEventListener('pointerleave', hide);
    svg.addEventListener('focus', () => moveTo(focusTs));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (event) => {
        const step = (span.to - span.from) / 60;
        if (event.key === 'ArrowLeft') moveTo(focusTs - step);
        else if (event.key === 'ArrowRight') moveTo(focusTs + step);
        else if (event.key === 'Home') moveTo(span.from);
        else if (event.key === 'End') moveTo(span.to);
        else if (event.key === 'Escape') hide();
        else return;
        event.preventDefault();
    });
}

// ---------- Data table ----------

// The highest reading of every visible series per local hour (one day) or day
// (longer ranges), newest first - the chart's data in readable form.
function renderHistoryTable() {
    const series = quotaSeries().filter((entry) => !isHidden(entry));
    const span = chartSpan();
    const hourly = state.history.range === '24h';
    const starts = [];
    const cursor = new Date(span.to * 1000);
    if (hourly) cursor.setMinutes(0, 0, 0);
    else cursor.setHours(0, 0, 0, 0);
    while (cursor.getTime() / 1000 > span.from - (hourly ? HOUR : DAY)) {
        starts.push(cursor.getTime() / 1000);
        if (hourly) cursor.setHours(cursor.getHours() - 1);
        else cursor.setDate(cursor.getDate() - 1);
    }
    const rows = starts.map((start, position) => {
        const end = position === 0 ? Infinity : starts[position - 1];
        const cells = series.map((entry) => {
            let max = null;
            for (const point of entry.points) {
                if (point.ts >= start && point.ts < end) max = max === null ? point.value : Math.max(max, point.value);
            }
            return max === null ? '-' : pct(max);
        });
        const date = new Date(start * 1000);
        const day = hourly ? clock(start) : date.toLocaleDateString([], { day: 'numeric', month: 'short' });
        const label = `${weekdayName(date)} ${day}`;
        return { label, cells };
    });
    const table = h('table', {},
        h('thead', {}, h('tr', {},
            h('th', { scope: 'col', text: hourly ? tr('table_hour', 'Hour') : tr('table_day', 'Day') }),
            ...series.map((entry) => h('th', { scope: 'col', text: columnName(entry) })))),
        h('tbody', {}, ...rows.map((row) => h('tr', {}, h('th', { scope: 'row', text: row.label }), ...row.cells.map((cell) => h('td', { text: cell }))))));
    const note = hourly ? tr('table_note_hour', 'Highest reading in each hour') : tr('table_note_day', 'Highest reading on each day');
    const rowCount = fmt(tr('rows', '{count} rows · {range}'), { count: rows.length, range: rangeName(state.history.range) });
    byId('historyTableBody').replaceChildren(
        h('p', { class: 'muted', text: `${note} · ${rowCount}` }),
        h('div', { class: 'table-scroll' }, table));
}

// ---------- Consumption bars ----------

function niceStep(raw) {
    const power = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-6)));
    for (const factor of [1, 2, 2.5, 5, 10]) if (factor * power >= raw) return factor * power;
    return 10 * power;
}

function renderConsumption() {
    const root = byId('consumptionChart');
    const consumption = state.history.consumption || { unit: 'day', starts: [], providers: [] };
    const hourly = consumption.unit === 'hour';
    byId('consumptionTitle').textContent = hourly ? tr('consumption_hourly', 'Quota used per hour') : tr('consumption_daily', 'Quota used per day');
    const shown = consumption.providers
        .filter((provider) => !state.hidden.has(provider.id))
        .sort((a, b) => providerRank(a.id) - providerRank(b.id));
    const labels = new Set(consumption.providers.map((provider) => provider.label));
    byId('consumptionMeta').textContent = labels.size === 1
        ? fmt(tr('consumption_meta', 'percentage points of the {label} quota, counted once'), { label: Array.from(labels)[0] })
        : tr('consumption_meta_mixed', "percentage points of each provider's longest quota, counted once");
    let max = 0;
    for (const provider of shown) for (const value of provider.values) max = Math.max(max, value);
    if (!shown.length || max <= 0) {
        root.replaceChildren(h('p', { class: 'muted empty', text: tr('waiting_history', 'Waiting for history data') }));
        return;
    }

    const width = Math.max(300, Math.floor(root.clientWidth));
    const height = 220;
    const margin = { top: 12, right: 8, bottom: 30, left: 40 };
    const count = consumption.starts.length;
    const step = niceStep(max / 4);
    const yMax = Math.max(step, Math.ceil(max / step) * step);
    const plotWidth = width - margin.left - margin.right;
    const plotBottom = height - margin.bottom;
    const y = (value) => plotBottom - (value / yMax) * (plotBottom - margin.top);
    const svg = s('svg', { class: 'chart', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': byId('consumptionTitle').textContent });
    for (let value = 0; value <= yMax + 1e-9; value += step) {
        svg.append(s('line', { class: value === 0 ? 'axis-line' : 'grid-line', x1: margin.left, x2: width - margin.right, y1: y(value), y2: y(value) }));
        svg.append(s('text', { class: 'tick', x: margin.left - 6, y: y(value) + 4, 'text-anchor': 'end', text: decimal(value, step < 1 ? 1 : 0) }));
    }

    const groupWidth = plotWidth / count;
    const bars = shown.length;
    const barWidth = Math.max(2, Math.min(22, (groupWidth * 0.78 - 2 * (bars - 1)) / bars));
    const inner = bars * barWidth + 2 * (bars - 1);
    const labelEvery = hourly ? (width < 520 ? 6 : 3) : count <= 7 ? 1 : (width < 520 ? 7 : 5);
    const hover = s('rect', { class: 'bar-hover', visibility: 'hidden', y: margin.top, height: plotBottom - margin.top, width: groupWidth });
    svg.append(hover);
    consumption.starts.forEach((start, index) => {
        const groupX = margin.left + index * groupWidth;
        const barsX = groupX + (groupWidth - inner) / 2;
        shown.forEach((provider, position) => {
            const value = provider.values[index] || 0;
            if (value <= 0.05) return;
            svg.append(s('path', { d: roundedTop(barsX + position * (barWidth + 2), y(value), barWidth, plotBottom), fill: providerColor(provider.id) }));
        });
        const date = new Date(start * 1000);
        if (index % labelEvery === 0) {
            const text = hourly ? pad(date.getHours()) : count <= 7 ? `${weekdayName(date)} ${date.getDate()}` : String(date.getDate());
            svg.append(s('text', { class: 'tick', x: groupX + groupWidth / 2, y: height - 10, 'text-anchor': 'middle', text }));
        }
        const title = hourly
            ? `${weekdayName(date)} ${clock(start)}-${clock(start + HOUR)}`
            : `${weekdayName(date)} ${date.toLocaleDateString([], { day: 'numeric', month: 'short' })}`;
        const rows = [h('div', { class: 'tip-head', text: title })];
        for (const provider of shown) {
            const amount = fmt(tr('pp', '{value} pp'), { value: decimal(provider.values[index] || 0) });
            rows.push(tipRow(provider.id, amount, providerLabel(provider.id)));
        }
        const hit = s('rect', { class: 'hit', x: groupX, y: margin.top, width: groupWidth, height: plotBottom - margin.top });
        hit.addEventListener('pointermove', (event) => {
            hover.setAttribute('x', groupX);
            hover.setAttribute('visibility', 'visible');
            showTip(rows, event.clientX, event.clientY);
        });
        hit.addEventListener('pointerleave', () => {
            hover.setAttribute('visibility', 'hidden');
            hideTip();
        });
        svg.append(hit);
    });
    root.replaceChildren(svg);
}

// ---------- This week vs the typical week ----------

const TYPICAL_MARGIN = { top: 18, bottom: 30, left: 44 };

// The current cycle of each provider's longest quota against its past cycles:
// grey past weeks, their median as the typical week, and this week with its
// forecast by the usual rhythm inside the range of the past weeks.
function renderTypicalWeek() {
    const weeks = [...((state.history.typical_week || {}).providers || [])];
    weeks.sort((a, b) => providerRank(a.id) - providerRank(b.id));
    if (!weeks.some((week) => week.id === state.typicalProvider)) state.typicalProvider = weeks.length ? weeks[0].id : null;
    byId('typicalControl').replaceChildren(...weeks.map((week) => h('button', {
        type: 'button',
        'aria-pressed': week.id === state.typicalProvider ? 'true' : 'false',
        onclick: () => {
            state.typicalProvider = week.id;
            writeStored('agentpulse-typical-provider', week.id);
            safely(renderTypicalWeek, 'typicalChart');
        },
    }, dot(week.id), providerLabel(week.id))));

    const root = byId('typicalChart');
    const note = byId('typicalNote');
    const week = weeks.find((entry) => entry.id === state.typicalProvider);
    if (!week) {
        root.replaceChildren(h('p', { class: 'muted empty', text: tr('waiting_history', 'Waiting for history data') }));
        note.textContent = '';
        return;
    }
    const predictions = (state.status.settings || {}).prediction_enabled !== false;
    const forecast = predictions && week.forecast.likely.length > 1 ? week.forecast : null;
    note.textContent = typicalNote(week, forecast);
    root.replaceChildren(typicalChart(root, week, forecast, note.textContent));
}

function typicalNote(week, forecast) {
    if (!week.past.length) return tr('typical_waiting', 'Your typical week appears once the history holds a full past week.');
    const parts = [];
    const typicalNow = reachedBy(week.typical, week.age);
    if (typicalNow !== null) {
        const values = { now: pct(week.utilization), typical: pct(typicalNow) };
        parts.push(fmt(tr('typical_verdict', '{now} now, typically {typical} by this point of the week.'), values));
    }
    parts.push(tr('typical_caption', 'Grey: your previous weeks. Thick grey: the typical week, their median.'));
    if (forecast) parts.push(tr('typical_caption_forecast', "Dashed: this week's forecast by your usual rhythm, shaded: the range of your past weeks."));
    return parts.join(' ');
}

// The value of the last point at or before an age, or null before the first.
function reachedBy(points, age) {
    let reached = null;
    for (const [pointAge, value] of points) {
        if (pointAge > age) break;
        reached = value;
    }
    return reached;
}

function typicalChart(root, week, forecast, label) {
    const width = Math.max(300, Math.floor(root.clientWidth));
    const narrow = width < 640;
    const margin = { ...TYPICAL_MARGIN, right: narrow ? 12 : 128 };
    const height = narrow ? 210 : 250;
    const plotRight = width - margin.right;
    const plotBottom = height - margin.bottom;
    const period = week.period_seconds;
    const x = (age) => margin.left + (Math.max(0, Math.min(period, age)) / period) * (plotRight - margin.left);
    const y = (value) => margin.top + (1 - Math.max(0, Math.min(100, value)) / 100) * (plotBottom - margin.top);
    const path = (points) => points.map(([age, value], index) => `${index ? 'L' : 'M'}${x(age).toFixed(1)},${y(value).toFixed(1)}`).join('');
    const color = providerColor(week.id);
    const svg = s('svg', { class: 'chart', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': label });

    for (const day of cycleDays(week)) {
        const left = x(day.from);
        const right = x(day.to);
        if (day.weekend) svg.append(s('rect', { class: 'band', x: left, y: margin.top, width: right - left, height: plotBottom - margin.top }));
        if (day.from > 0) svg.append(s('line', { class: 'grid-line', x1: left, x2: left, y1: margin.top, y2: plotBottom }));
        if (right - left >= 28) svg.append(s('text', { class: 'tick', x: (left + right) / 2, y: height - 10, 'text-anchor': 'middle', text: day.name }));
    }
    for (const value of [0, 50, 100]) {
        svg.append(s('line', { class: value === 0 ? 'axis-line' : 'grid-line', x1: margin.left, x2: plotRight, y1: y(value), y2: y(value) }));
        svg.append(s('text', { class: 'tick', x: margin.left - 8, y: y(value) + 4, 'text-anchor': 'end', text: value === 100 ? '100%' : String(value) }));
    }

    for (const points of week.past) if (points.length > 1) svg.append(s('path', { class: 'ghost-line', d: path(points) }));
    if (week.typical.length > 1) svg.append(s('path', { class: 'typical-line', d: path(week.typical) }));
    if (forecast) {
        const lower = forecast.low.slice().reverse().map(([age, value]) => `L${x(age).toFixed(1)},${y(value).toFixed(1)}`).join('');
        svg.append(s('path', { class: 'cone', d: `${path(forecast.high)}${lower}Z`, fill: color }));
        svg.append(s('path', { class: 'line forecast-line', d: path(forecast.likely), stroke: color }));
    }
    svg.append(s('line', { class: 'now-line', x1: x(week.age), x2: x(week.age), y1: margin.top, y2: plotBottom }));
    if (week.current.length > 1) svg.append(s('path', { class: 'line current-line', d: path(week.current), stroke: color }));
    svg.append(s('circle', { class: 'now-dot', cx: x(week.age), cy: y(week.utilization), r: 4.5, fill: color }));
    const early = x(week.age) - margin.left < 90;
    svg.append(s('text', {
        class: 'point-label', x: x(week.age) + (early ? 8 : -8), y: y(week.utilization) - 9, 'text-anchor': early ? 'start' : 'end',
        text: fmt(tr('typical_now', 'now {pct}'), { pct: pct(week.utilization) }),
    }));
    if (!narrow) typicalEndLabels(svg, week, forecast, y, plotRight);
    return svg;
}

// Where this week's forecast and the typical week end at the reset, nudged apart so they never overlap.
function typicalEndLabels(svg, week, forecast, y, plotRight) {
    const labels = [];
    if (forecast) {
        const end = forecast.likely[forecast.likely.length - 1][1];
        labels.push({ y: y(end), strong: true, text: fmt(tr('forecast_at_reset', '~{pct}% at reset'), { pct: Math.round(end) }) });
    }
    if (week.typical.length) {
        const end = week.typical[week.typical.length - 1][1];
        labels.push({ y: y(end), strong: false, text: fmt(tr('typically', 'typically {pct}'), { pct: pct(end) }) });
    }
    labels.sort((a, b) => a.y - b.y);
    labels.forEach((label, index) => {
        if (index) label.y = Math.max(label.y, labels[index - 1].y + 15);
    });
    for (const label of labels) {
        svg.append(s('text', { class: label.strong ? 'end-label end-strong' : 'end-label', x: plotRight + 10, y: label.y + 4, text: label.text }));
    }
}

// ---------- Sessions and time at the limit ----------

const SESSION_LANE = 34;

// Every provider's session windows of the last week as bars, their time at the
// limit in red, a tile per provider with its blocks of the last 30 days, and
// the planner's advice for the first session of a workday.
function renderSessions() {
    const sessions = state.history.sessions || { days: 7, from: 0, providers: [] };
    const list = [...sessions.providers];
    list.sort((a, b) => providerRank(a.id) - providerRank(b.id));
    const chart = byId('sessionsChart');
    if (!list.length) {
        byId('sessionStats').replaceChildren();
        byId('sessionPlanner').replaceChildren();
        chart.replaceChildren(h('p', { class: 'muted empty', text: tr('waiting_history', 'Waiting for history data') }));
        return;
    }
    byId('sessionStats').replaceChildren(...list.map(sessionStat));
    chart.replaceChildren(sessionsChart(chart, sessions, list));
    byId('sessionPlanner').replaceChildren(...list.map(plannerNote).filter(Boolean));
}

function sessionStat(provider) {
    const period = fmt(tr('blocks_period', '{provider} · last 30 days'), { provider: providerLabel(provider.id) });
    const label = h('span', { class: 'stat-label' }, dot(provider.id), period);
    if (!provider.blocked.count) {
        return h('div', { class: 'stat' }, label,
            h('strong', { text: tr('blocks_none', 'Never at the limit') }),
            h('small', { text: tr('blocks_none_detail', 'The session limit never ran out') }));
    }
    const detail = { duration: durationText(provider.blocked.seconds), week: provider.blocked_week.count };
    return h('div', { class: 'stat stat-blocked' }, label,
        h('strong', { text: fmt(tr('blocks_count', '{n}× at the limit'), { n: provider.blocked.count }) }),
        h('small', { text: fmt(tr('blocks_detail', '{duration} without quota · {week}× in the last 7 days'), detail) }));
}

function sessionsChart(root, sessions, list) {
    const width = Math.max(300, Math.floor(root.clientWidth));
    const narrow = width < 640;
    const margin = { top: 6, right: 10, bottom: 28, left: narrow ? 52 : 64 };
    const lanes = list.length * SESSION_LANE;
    const height = margin.top + lanes + margin.bottom;
    const from = sessions.from;
    const to = from + sessions.days * DAY;
    const now = state.status.now || Date.now() / 1000;
    const plotRight = width - margin.right;
    const x = (ts) => margin.left + ((Math.max(from, Math.min(to, ts)) - from) / (to - from)) * (plotRight - margin.left);
    const svg = s('svg', {
        class: 'chart', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img',
        'aria-label': tr('sessions_label', 'Session windows of the last 7 days and the time at the limit'),
    });

    for (const [a, b] of bands(from, to, true)) svg.append(s('rect', { class: 'band', x: x(a), y: margin.top, width: x(b) - x(a), height: lanes }));
    const day = new Date(from * 1000);
    while (day.getTime() / 1000 < to) {
        const start = day.getTime() / 1000;
        const next = new Date(day);
        next.setDate(next.getDate() + 1);
        if (start > from) svg.append(s('line', { class: 'grid-line', x1: x(start), x2: x(start), y1: margin.top, y2: margin.top + lanes }));
        const end = next.getTime() / 1000;
        const text = narrow ? weekdayName(day) : `${weekdayName(day)} ${day.getDate()}`;
        const tickClass = start <= now && now < end ? 'tick today' : 'tick';
        svg.append(s('text', { class: tickClass, x: (x(start) + x(end)) / 2, y: height - 9, 'text-anchor': 'middle', text }));
        day.setDate(day.getDate() + 1);
    }

    list.forEach((provider, index) => {
        const top = margin.top + index * SESSION_LANE;
        svg.append(s('text', { class: 'tick', x: margin.left - 8, y: top + SESSION_LANE / 2 + 4, 'text-anchor': 'end', text: providerLabel(provider.id) }));
        for (const window of provider.windows) {
            const left = x(window.start);
            const right = x(Math.min(now, window.end));
            if (right <= left) continue;
            svg.append(s('rect', {
                x: left, y: top + 8, width: Math.max(1.5, right - left), height: SESSION_LANE - 16, rx: 3,
                fill: providerColor(provider.id), 'fill-opacity': (0.25 + 0.6 * Math.min(100, window.peak) / 100).toFixed(2),
            }));
            if (window.blocked_at) {
                const blocked = x(window.blocked_at);
                const span = Math.max(1.5, right - blocked);
                svg.append(s('rect', { class: 'session-blocked', x: blocked, y: top + 8, width: span, height: SESSION_LANE - 16, rx: 2 }));
            }
            const hit = s('rect', { class: 'hit', x: left - 2, y: top + 4, width: Math.max(6, right - left + 4), height: SESSION_LANE - 8 });
            const content = sessionTip(provider, window);
            hit.addEventListener('pointermove', (event) => showTip(content, event.clientX, event.clientY));
            hit.addEventListener('pointerleave', hideTip);
            svg.append(hit);
        }
    });
    if (now > from && now < to) svg.append(s('line', { class: 'cross-line', x1: x(now), x2: x(now), y1: margin.top - 2, y2: margin.top + lanes }));
    return svg;
}

function sessionTip(provider, window) {
    const date = new Date(window.start * 1000);
    const rows = [
        h('div', { class: 'tip-head', text: `${providerLabel(provider.id)} · ${weekdayName(date)} ${date.getDate()}` }),
        h('div', { text: fmt(tr('session_tip', '{start}-{end} · peak {pct}'), {
            start: clock(window.start), end: clock(window.end), pct: pct(window.peak),
        }) }),
    ];
    if (window.blocked_at) {
        rows.push(h('div', { text: fmt(tr('session_blocked_tip', 'at the limit {from}-{to}'), { from: clock(window.blocked_at), to: clock(window.end) }) }));
    }
    return rows;
}

// Advice for a provider that reached its session limit: when its first
// session of a workday usually starts and resets, how often and how early it
// runs out, and the earlier start that would bring the reset to that point.
function plannerNote(provider) {
    const plan = provider.planner;
    if (!plan || !provider.blocked.count) return null;
    const parts = [fmt(tr('planner_start', 'Your first {provider} session on workdays usually starts around {start} and resets around {reset}.'), {
        provider: providerLabel(provider.id), start: dayClock(plan.start), reset: dayClock(plan.reset),
    })];
    if (plan.blocked) {
        parts.push(fmt(tr('planner_blocked', 'It ran out on {blocked} of the last {days} workdays, typically {lead} before the reset.'), {
            blocked: plan.blocked, days: plan.days, lead: durationText(plan.lead),
        }));
    }
    if (plan.suggested !== null && plan.suggested !== undefined) {
        parts.push(fmt(tr('planner_move', 'A first message around {suggested} would move the reset to about when you run out.'), {
            suggested: dayClock(plan.suggested),
        }));
    }
    return h('p', { class: 'planner-note' }, dot(provider.id), h('span', { text: parts.join(' ') }));
}

// HH:MM of a time given as seconds after local midnight.
function dayClock(seconds) {
    const minutes = Math.round(seconds / 60) % (24 * 60);
    return `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
}

// The local days of the current cycle as spans of seconds into it, named by weekday.
function cycleDays(week) {
    const days = [];
    const end = week.start + week.period_seconds;
    let from = week.start;
    while (from < end) {
        const date = new Date(from * 1000);
        const next = new Date(date);
        next.setHours(24, 0, 0, 0);
        const to = Math.min(end, next.getTime() / 1000);
        days.push({ from: from - week.start, to: to - week.start, name: weekdayName(date), weekend: date.getDay() === 0 || date.getDay() === 6 });
        from = to;
    }
    return days;
}

function roundedTop(x, top, width, base) {
    const radius = Math.max(0, Math.min(4, width / 2, base - top));
    return `M${x},${base}V${top + radius}Q${x},${top} ${x + radius},${top}H${x + width - radius}Q${x + width},${top} ${x + width},${top + radius}V${base}Z`;
}

// ---------- Heatmap ----------

function renderHeatmap() {
    const settings = state.status.settings || {};
    const section = byId('heatmapSection');
    section.hidden = settings.heatmap_enabled === false;
    if (section.hidden) return;

    const heatmap = state.history.heatmap || { days: 28, providers: [] };
    const list = [...heatmap.providers].sort((a, b) => providerRank(a.id) - providerRank(b.id));
    if (!list.some((provider) => provider.id === state.heatProvider)) state.heatProvider = list.length ? list[0].id : null;
    byId('heatmapControl').replaceChildren(...list.map((provider) => h('button', {
        type: 'button',
        'aria-pressed': provider.id === state.heatProvider ? 'true' : 'false',
        onclick: () => {
            state.heatProvider = provider.id;
            writeStored('agentpulse-heat-provider', provider.id);
            safely(renderHeatmap, 'heatmapGrid');
        },
    }, dot(provider.id), providerLabel(provider.id))));

    const grid = byId('heatmapGrid');
    const chosen = list.find((provider) => provider.id === state.heatProvider);
    if (!chosen) {
        grid.replaceChildren(h('p', { class: 'muted empty', text: tr('waiting_history', 'Waiting for history data') }));
        byId('heatmapNote').textContent = '';
        return;
    }

    let max = 0;
    let peak = null;
    chosen.cells.forEach((row, weekday) => row.forEach((value, hour) => {
        if (value > max) {
            max = value;
            peak = { weekday, hour, value };
        }
    }));
    const color = providerColor(chosen.id);
    const nodes = [h('span', { class: 'heat-corner' })];
    for (let hour = 0; hour < 24; hour++) nodes.push(h('span', { class: 'heat-hour', text: hour % 3 === 0 ? String(hour) : '' }));
    chosen.cells.forEach((row, weekday) => {
        const name = tr(`weekday_${weekday}`, String(weekday));
        nodes.push(h('span', { class: 'heat-day', text: name }));
        row.forEach((value, hour) => {
            const cell = h('span', { class: 'heat-cell' });
            if (value > 0 && max > 0) cell.style.background = `color-mix(in srgb, ${color} ${Math.round(18 + 82 * value / max)}%, var(--heat-empty))`;
            const text = fmt(tr('heatmap_cell', '{day} {start}-{end} · average {value} pp'), {
                day: name, start: `${pad(hour)}:00`, end: `${pad((hour + 1) % 24)}:00`, value: decimal(value),
            });
            const content = [h('div', { class: 'tip-head', text: providerLabel(chosen.id) }), h('div', { text })];
            cell.addEventListener('pointermove', (event) => showTip(content, event.clientX, event.clientY));
            cell.addEventListener('pointerleave', hideTip);
            nodes.push(cell);
        });
    });
    grid.replaceChildren(...nodes);
    grid.setAttribute('role', 'img');
    const scale = byId('heatmapScale');
    scale.style.background = `linear-gradient(90deg, var(--heat-empty), ${color})`;
    if (peak) {
        const note = fmt(tr('heatmap_peak', 'Busiest: {day} {start}-{end}, on average {value} pp of the {label} quota. Last {days} days.'), {
            day: tr(`weekday_${peak.weekday}`, ''), start: `${pad(peak.hour)}:00`, end: `${pad((peak.hour + 1) % 24)}:00`,
            value: decimal(peak.value), label: chosen.label, days: heatmap.days,
        });
        byId('heatmapNote').textContent = note;
        grid.setAttribute('aria-label', note);
    } else {
        byId('heatmapNote').textContent = tr('waiting_history', 'Waiting for history data');
        grid.setAttribute('aria-label', byId('heatmapNote').textContent);
    }
}

// ---------- Footer ----------

function renderFooter() {
    const status = state.status;
    const parts = [fmt(tr('footer_privacy', 'Runs only on {host} - no analytics, tokens never leave the app'), { host: status.privacy.bind })];
    for (const provider of providers()) {
        const versions = (provider.installations || []).map((item) => `${item.name} ${item.version}`).join(', ') || tr('not_detected', 'not detected');
        parts.push(`${provider.label}: ${versions}`);
    }
    parts.push(`${status.app.name} ${status.app.version}`);
    byId('siteFooter').textContent = parts.join(' · ');
}

// ---------- Settings drawer ----------

let drawerOpener = null;

function openDrawer() {
    const drawer = byId('settingsDrawer');
    drawerOpener = document.activeElement;
    drawer.removeAttribute('inert');
    drawer.setAttribute('aria-hidden', 'false');
    drawer.classList.add('open');
    byId('drawerBackdrop').classList.add('open');
    byId('openSettings').setAttribute('aria-expanded', 'true');
    document.body.classList.add('drawer-open');
    byId('settingsStatus').textContent = '';
    loadSettings();
    requestAnimationFrame(() => byId('closeSettings').focus());
}

function closeDrawer() {
    const drawer = byId('settingsDrawer');
    if (!drawer.classList.contains('open')) return;
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    drawer.setAttribute('inert', '');
    byId('drawerBackdrop').classList.remove('open');
    byId('openSettings').setAttribute('aria-expanded', 'false');
    document.body.classList.remove('drawer-open');
    if (drawerOpener && drawerOpener.focus) drawerOpener.focus();
}

// Keeps Tab inside the open drawer, the way a modal dialog behaves.
function trapFocus(event) {
    const drawer = byId('settingsDrawer');
    if (event.key !== 'Tab' || !drawer.classList.contains('open')) return;
    const candidates = Array.from(drawer.querySelectorAll('button, input, textarea, select, a[href]'));
    const focusable = candidates.filter((node) => !node.disabled && node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
        last.focus();
        event.preventDefault();
    } else if (!event.shiftKey && document.activeElement === last) {
        first.focus();
        event.preventDefault();
    }
}

async function loadSettings() {
    let data;
    try {
        const response = await fetch('/api/settings', { cache: 'no-store', headers: { 'X-AgentsPulse-Token': authToken } });
        if (!response.ok) {
            byId('settingsStatus').textContent = tr('session_expired', 'session expired - reopen the dashboard from the tray menu');
            return;
        }
        data = await response.json();
    } catch {
        return;
    }
    const settings = data.settings || {};
    byId('autostartEnabled').checked = !!settings.autostart;
    byId('codexEnabled').checked = !!settings.codex_enabled;
    byId('kimiEnabled').checked = !!settings.kimi_enabled;
    byId('tooltipFields').value = (settings.tooltip_fields || []).join(', ');
    byId('thresholdClaude5h').value = (settings.alert_thresholds_five_hour || []).join(', ');
    byId('thresholdClaude7d').value = (settings.alert_thresholds_seven_day || []).join(', ');
    byId('thresholdCodex5h').value = (settings.alert_thresholds_codex_five_hour || []).join(', ');
    byId('thresholdCodex7d').value = (settings.alert_thresholds_codex_seven_day || []).join(', ');
    byId('thresholdKimi5h').value = (settings.alert_thresholds_kimi_five_hour || []).join(', ');
    byId('thresholdKimi7d').value = (settings.alert_thresholds_kimi_seven_day || []).join(', ');
    byId('predictionEnabled').checked = settings.prediction_enabled !== false;
    byId('predictionDayEnd').value = settings.prediction_day_end_time || '18:00';
    byId('heatmapEnabled').checked = settings.heatmap_enabled !== false;
    byId('awaySummaryEnabled').checked = settings.away_summary_enabled !== false;
    byId('spikeAlertEnabled').checked = settings.spike_alert_enabled !== false;
    byId('quietHoursEnabled').checked = !!settings.quiet_hours_enabled;
    byId('quietHoursStart').value = settings.quiet_hours_start || '22:00';
    byId('quietHoursEnd').value = settings.quiet_hours_end || '08:00';
    for (const input of document.querySelectorAll('input[name="iconStyle"]')) input.checked = input.value === (settings.icon_style || 'bars');
    // One command per line: each runs on its own, exactly like the array in the settings file.
    byId('resetCommand').value = (settings.on_reset_command || []).join('\n');
    byId('thresholdCommand').value = (settings.on_threshold_command || []).join('\n');
    byId('spikeCommand').value = (settings.on_spike_command || []).join('\n');
    byId('statuslineEnabled').checked = !!settings.statusline_enabled;
    const workdays = settings.budget_workdays || [];
    for (const input of document.querySelectorAll('input[name="budgetDay"]')) input.checked = workdays.includes(Number(input.value));
    loadStatuslinePreview();
}

function parseList(value) {
    return value.split(',').map((item) => item.trim()).filter(Boolean);
}

function parseLines(value) {
    return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function parseNumbers(value) {
    return parseList(value).map(Number).filter((number) => Number.isFinite(number));
}

async function saveSettings(event) {
    event.preventDefault();
    const style = document.querySelector('input[name="iconStyle"]:checked');
    const payload = {
        autostart: byId('autostartEnabled').checked,
        codex_enabled: byId('codexEnabled').checked,
        kimi_enabled: byId('kimiEnabled').checked,
        tooltip_fields: parseList(byId('tooltipFields').value),
        alert_thresholds_five_hour: parseNumbers(byId('thresholdClaude5h').value),
        alert_thresholds_seven_day: parseNumbers(byId('thresholdClaude7d').value),
        alert_thresholds_codex_five_hour: parseNumbers(byId('thresholdCodex5h').value),
        alert_thresholds_codex_seven_day: parseNumbers(byId('thresholdCodex7d').value),
        alert_thresholds_kimi_five_hour: parseNumbers(byId('thresholdKimi5h').value),
        alert_thresholds_kimi_seven_day: parseNumbers(byId('thresholdKimi7d').value),
        prediction_enabled: byId('predictionEnabled').checked,
        prediction_day_end_time: byId('predictionDayEnd').value || '18:00',
        heatmap_enabled: byId('heatmapEnabled').checked,
        away_summary_enabled: byId('awaySummaryEnabled').checked,
        spike_alert_enabled: byId('spikeAlertEnabled').checked,
        quiet_hours_enabled: byId('quietHoursEnabled').checked,
        quiet_hours_start: byId('quietHoursStart').value || '22:00',
        quiet_hours_end: byId('quietHoursEnd').value || '08:00',
        on_reset_command: parseLines(byId('resetCommand').value),
        on_threshold_command: parseLines(byId('thresholdCommand').value),
        on_spike_command: parseLines(byId('spikeCommand').value),
        statusline_enabled: byId('statuslineEnabled').checked,
        budget_workdays: Array.from(document.querySelectorAll('input[name="budgetDay"]:checked'), (input) => Number(input.value)),
    };
    if (style) payload.icon_style = style.value;
    const result = await postJson('/api/settings', payload);
    const saved = result.restart_required
        ? tr('restart_required', 'settings saved - restart the app to apply the Codex or Kimi monitoring change')
        : tr('saved', 'settings saved');
    byId('settingsStatus').textContent = result.ok
        ? saved
        : fmt(tr('error', 'error: {errors}'), { errors: (result.errors || []).join(', ') });
    if (result.ok) {
        state.historyKey = '';
        refresh();
        loadStatuslinePreview();
    }
}

// The command points at this dashboard's own address, so it stays right when
// the default port was taken and the server started on the next free one.
// curl.exe, not curl: in Windows PowerShell "curl" is Invoke-WebRequest.
function statuslineSnippet() {
    const command = `curl.exe -sf --max-time 1 ${location.origin}/api/statusline`;
    return `"statusLine": ${JSON.stringify({ type: 'command', command }, null, 2)}`;
}

// Shows the line exactly as Claude Code will, or nothing while the feature is off.
async function loadStatuslinePreview() {
    const preview = byId('statuslinePreview');
    let text = '';
    try {
        const response = await fetch('/api/statusline?color=0', { cache: 'no-store' });
        if (response.ok) text = (await response.text()).trim();
    } catch {
        text = '';
    }
    preview.textContent = text;
    preview.hidden = !text;
}

async function copyStatusline() {
    const snippet = byId('statuslineSnippet');
    try {
        await navigator.clipboard.writeText(snippet.textContent);
        byId('settingsStatus').textContent = tr('copied', 'copied to the clipboard');
    } catch {
        // Without clipboard access the text is selected, ready for Ctrl+C.
        const range = document.createRange();
        range.selectNodeContents(snippet);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
    }
}

async function testEvent(eventName) {
    const result = await postJson('/api/test-event', { event: eventName });
    byId('settingsStatus').textContent = result.ok
        ? fmt(tr('test_fired', 'test {event} fired'), { event: eventName })
        : fmt(tr('test_failed', 'test failed: {errors}'), { errors: (result.errors || []).join(', ') || tr('unknown_error', 'unknown error') });
}

// ---------- Wiring ----------

function selectRange(range) {
    if (!RANGE_SECONDS[range] || range === state.range) return;
    state.range = range;
    state.historyKey = '';
    writeStored('agentpulse-range', range);
    syncRangeControl();
    refresh();
}

function syncRangeControl() {
    for (const button of byId('rangeControl').querySelectorAll('button')) {
        button.setAttribute('aria-pressed', button.dataset.range === state.range ? 'true' : 'false');
    }
    byId('exportCsv').href = `/api/history.csv?range=${encodeURIComponent(state.range)}`;
}

for (const button of byId('rangeControl').querySelectorAll('button')) button.addEventListener('click', () => selectRange(button.dataset.range));
byId('openSettings').addEventListener('click', openDrawer);
byId('closeSettings').addEventListener('click', closeDrawer);
byId('drawerBackdrop').addEventListener('click', closeDrawer);
byId('settingsForm').addEventListener('submit', saveSettings);
byId('testReset').addEventListener('click', () => testEvent('reset'));
byId('testThreshold').addEventListener('click', () => testEvent('threshold'));
byId('testSpike').addEventListener('click', () => testEvent('spike'));
byId('copyStatusline').addEventListener('click', copyStatusline);
byId('statuslineSnippet').textContent = statuslineSnippet();
byId('historyTable').addEventListener('toggle', () => {
    if (byId('historyTable').open && state.history) safely(renderHistoryTable, 'historyTableBody');
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeDrawer();
    trapFocus(event);
});

// Charts redraw at their container's width, so text keeps its size.
let resizeFrame = 0;
let lastWidth = 0;
new ResizeObserver(() => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
        const width = byId('historyChart').clientWidth;
        if (width === lastWidth || !state.status || !state.history) return;
        lastWidth = width;
        renderCharts();
    });
}).observe(document.querySelector('main'));

// Nothing is fetched while the tab is in the background; coming back
// refreshes immediately instead of waiting for the next interval.
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
});

syncRangeControl();
loadI18n().then(refresh);
setInterval(refresh, 15000);
setInterval(tickLive, 1000);
