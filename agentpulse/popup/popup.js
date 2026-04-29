let nodes = {};
let strings = {};
let providers = { claude: null, codex: null };
let selectedTab = 'all';
let statusTimer = null;
let statusModel = {};

function byId(id) {
  return document.getElementById(id);
}

function init(config) {
  applyTheme(config.colors);
  strings = config.t;
  bindStaticText(config);
  bindNodes();
  bindActions(config.codex_enabled);
  providers.claude = config.data;
  providers.codex = config.codex_data;
  render(currentData());
  requestAnimationFrame(() => document.body.classList.add('open'));
}

function applyTheme(colors) {
  const style = document.documentElement.style;
  Object.entries(colors).forEach(([key, value]) => {
    style.setProperty(`--${key.replaceAll('_', '-')}`, value);
  });
}

function bindStaticText(config) {
  byId('title').textContent = strings.title;
  byId('headingAccount').textContent = strings.account;
  byId('labelEmail').textContent = strings.email;
  byId('labelPlan').textContent = strings.plan;
  byId('headingUsage').textContent = strings.usage;
  byId('headingExtraUsage').textContent = strings.extra_usage;
  byId('changelogLink').textContent = strings.changelog;
  byId('appVersion').textContent = config.app_version;
}

function bindNodes() {
  nodes = {
    account: byId('accountSection'),
    emailRow: byId('emailRow'),
    emailValue: byId('emailValue'),
    planRow: byId('planRow'),
    planValue: byId('planValue'),
    usage: byId('usageSection'),
    bars: byId('usageBars'),
    extra: byId('extraSection'),
    extraSpent: byId('extraSpent'),
    extraPct: byId('extraPct'),
    extraFill: byId('extraFill'),
    install: byId('installSection'),
    installTitle: byId('headingInstall'),
    installRows: byId('installRows'),
    status: byId('statusSection'),
    statusText: byId('statusText'),
    tabs: byId('tabBar'),
  };
}

function bindActions(codexEnabled) {
  byId('closeBtn').addEventListener('click', () => pywebview.api.close());
  byId('changelogLink').addEventListener('click', () => pywebview.api.open_url());
  if (!codexEnabled) return;
  nodes.tabs.classList.remove('hidden');
  nodes.tabs.querySelectorAll('.tab-btn').forEach((button) => {
    button.addEventListener('click', () => chooseTab(button.dataset.tab));
  });
}

function chooseTab(tab) {
  if (tab === selectedTab) return;
  selectedTab = tab;
  nodes.tabs.querySelectorAll('.tab-btn').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === tab);
  });
  render(currentData());
}

function updateBothData(claudeData, codexData) {
  providers.claude = claudeData;
  providers.codex = codexData;
  render(currentData());
}

function currentData() {
  if (selectedTab === 'all') return combinedData();
  return providers[selectedTab];
}

function combinedData() {
  const claude = providers.claude || {};
  const codex = providers.codex || {};
  const usage = [];
  const left = claude.usage || [];
  const right = codex.usage || [];
  const count = Math.max(left.length, right.length);
  for (let index = 0; index < count; index += 1) {
    if (left[index]) usage.push({ ...left[index], provider: 'Claude', label: left[index].label });
    if (right[index]) usage.push({ ...right[index], provider: 'Codex', label: right[index].label });
  }
  return {
    profile: null,
    usage,
    extra: null,
    installations: [],
    status: olderStatus(claude.status, codex.status),
  };
}

function olderStatus(a, b) {
  if (!a) return b || null;
  if (!b) return a;
  if (a.last_success_time !== undefined && b.last_success_time !== undefined) {
    return a.last_success_time <= b.last_success_time ? a : b;
  }
  return a;
}

function render(data) {
  data = data || {};
  renderProfile(data.profile);
  renderUsage(data.usage || []);
  renderExtra(data.extra);
  renderInstallations(data.installations || []);
  renderStatus(data.status);
}

function renderProfile(profile) {
  const visible = !!profile;
  nodes.account.classList.toggle('visible', visible);
  if (!visible) return;
  nodes.emailValue.textContent = profile.email || '';
  nodes.planValue.textContent = profile.plan || '';
  nodes.emailRow.style.display = profile.email ? '' : 'none';
  nodes.planRow.style.display = profile.plan ? '' : 'none';
}

function renderUsage(entries) {
  nodes.usage.classList.toggle('visible', entries.length > 0);
  if (!entries.length) return;
  if (nodes.bars.children.length !== entries.length) {
    nodes.bars.replaceChildren(...entries.map(makeBar));
    requestAnimationFrame(() => entries.forEach((entry, index) => {
      nodes.bars.children[index].querySelector('.bar-fill').style.width = `${entry.fill_pct * 100}%`;
    }));
  } else {
    entries.forEach((entry, index) => updateBar(nodes.bars.children[index], entry));
  }
}

function renderExtra(extra) {
  nodes.extra.classList.toggle('visible', !!extra);
  if (!extra) return;
  nodes.extraSpent.textContent = extra.spent_text;
  nodes.extraPct.textContent = extra.pct_text;
  nodes.extraFill.style.width = `${extra.fill_pct * 100}%`;
}

function renderInstallations(items) {
  nodes.install.classList.toggle('visible', items.length > 0);
  if (!items.length) return;
  nodes.installTitle.textContent = selectedTab === 'codex' ? strings.codex_cli : strings.claude_code;
  nodes.installRows.replaceChildren(...items.map((item) => {
    const row = document.createElement('div');
    const name = document.createElement('dt');
    const version = document.createElement('dd');
    name.textContent = item.name;
    version.textContent = item.version;
    row.append(name, version);
    return row;
  }));
}

function renderStatus(status) {
  if (statusTimer) clearInterval(statusTimer);
  statusTimer = null;
  nodes.status.classList.toggle('visible', !!status);
  if (!status) return;
  if (status.last_success_time !== undefined) {
    statusModel = {
      lastSuccessTime: status.last_success_time,
      nextPollTime: status.next_poll_time,
      refreshing: status.refreshing,
      error: status.error,
    };
    nodes.status.classList.toggle('error', !!status.error);
    tickStatus();
    statusTimer = setInterval(tickStatus, 1000);
    return;
  }
  statusModel = {};
  nodes.statusText.textContent = status.text || '';
  nodes.status.classList.toggle('error', !!status.is_error);
}

function tickStatus() {
  if (!statusModel.lastSuccessTime) return;
  const now = Date.now() / 1000;
  const age = Math.max(0, Math.floor(now - statusModel.lastSuccessTime));
  const stale = !!statusModel.nextPollTime && now > statusModel.nextPollTime + 30;
  nodes.usage.classList.toggle('stale', stale);
  nodes.extra.classList.toggle('stale', stale);
  const parts = [durationSince(age)];
  if (statusModel.refreshing) {
    parts.push(strings.status_refreshing);
  } else if (statusModel.error) {
    parts.push(statusModel.error);
  } else if (age >= 60 && statusModel.nextPollTime) {
    const wait = Math.max(0, Math.floor(statusModel.nextPollTime - now));
    if (wait > 0) parts.push(strings.status_next_update.replace('{duration}', countdown(wait)));
  }
  nodes.statusText.textContent = parts.join(' \u00b7 ');
}

function durationSince(seconds) {
  if (seconds < 60) return strings.status_updated_s.replace('{s}', seconds);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  const duration = hours > 0
    ? strings.duration_hm.replace('{h}', hours).replace('{m}', remainder)
    : strings.duration_m.replace('{m}', minutes);
  return strings.status_updated.replace('{duration}', duration);
}

function countdown(seconds) {
  if (seconds < 60) return strings.duration_s.replace('{s}', seconds);
  const minutes = Math.ceil(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return hours > 0
    ? strings.duration_hm.replace('{h}', hours).replace('{m}', remainder)
    : strings.duration_m.replace('{m}', minutes);
}

function makeBar(entry) {
  const wrapper = document.createElement('div');
  wrapper.className = 'usage-entry';
  wrapper.classList.toggle('warn', entry.warn);
  const header = document.createElement('div');
  header.className = 'bar-header';
  const labelWrap = document.createElement('span');
  labelWrap.className = 'bar-label';
  const provider = document.createElement('span');
  provider.className = 'provider-badge';
  const label = document.createElement('span');
  const percent = document.createElement('span');
  percent.className = 'bar-pct';
  provider.textContent = entry.provider || '';
  provider.classList.toggle('hidden', !entry.provider);
  label.textContent = entry.label;
  labelWrap.append(provider, label);
  percent.textContent = entry.pct_text;
  header.append(labelWrap, percent);
  const track = document.createElement('div');
  track.className = 'bar-container';
  const fill = document.createElement('div');
  fill.className = 'bar-fill';
  fill.classList.toggle('warn', entry.warn);
  fill.style.width = '0%';
  track.append(fill);
  addMarkers(track, entry);
  wrapper.append(header, track);
  setResetText(wrapper, entry);
  return wrapper;
}

function updateBar(wrapper, entry) {
  wrapper.classList.toggle('warn', entry.warn);
  const provider = wrapper.querySelector('.provider-badge');
  provider.textContent = entry.provider || '';
  provider.classList.toggle('hidden', !entry.provider);
  wrapper.querySelector('.bar-label span:last-child').textContent = entry.label;
  wrapper.querySelector('.bar-pct').textContent = entry.pct_text;
  const fill = wrapper.querySelector('.bar-fill');
  fill.style.width = `${entry.fill_pct * 100}%`;
  fill.classList.toggle('warn', entry.warn);
  const track = wrapper.querySelector('.bar-container');
  track.querySelectorAll('.bar-divider,.bar-marker').forEach((node) => node.remove());
  addMarkers(track, entry);
  setResetText(wrapper, entry);
}

function addMarkers(track, entry) {
  (entry.midnights || []).forEach((position) => {
    const divider = document.createElement('div');
    divider.className = 'bar-divider';
    divider.style.left = `calc(${position * 100}% - 1px)`;
    track.append(divider);
  });
  if (entry.marker_rel !== null) {
    const marker = document.createElement('div');
    marker.className = 'bar-marker';
    marker.style.left = `calc(${entry.marker_rel * 100}% - 1px)`;
    track.append(marker);
  }
}

function setResetText(wrapper, entry) {
  const text = entry.reset_text && entry.burn_text
    ? `${entry.reset_text} \u00b7 ${entry.burn_text}`
    : (entry.reset_text || entry.burn_text || '');
  let node = wrapper.querySelector('.reset-text');
  if (!text) {
    if (node) node.remove();
    return;
  }
  if (!node) {
    node = document.createElement('div');
    node.className = 'reset-text';
    wrapper.append(node);
  }
  node.textContent = text;
}

new ResizeObserver(() => {
  const height = document.body.scrollHeight;
  if (window.pywebview?.api?.report_height) {
    pywebview.api.report_height(height);
  }
}).observe(document.body);
