let nodes = {};
let strings = {};
let providers = {};
let providerOrder = [];
let providerLabels = {};
let providerInstallTitles = {};
let selectedTab = 'all';
let statusTimer = null;
let statusModel = {};
let popupSettings = { show_install_section: false, email_display: 'show' };

function byId(id) {
  return document.getElementById(id);
}

function init(config) {
  applyTheme(config.colors);
  strings = config.t;
  bindStaticText(config);
  bindNodes();
  setProviders(config.providers || []);
  bindActions(providerOrder.length > 1);
  applyPopupSettings(config.popup_settings || {});
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
  byId('headingSettings').textContent = strings.settings_panel;
  byId('labelToggleInstall').textContent = strings.show_install_label;
  byId('labelEmailDisplay').textContent = strings.email_display_label;
  byId('segShow').textContent = strings.email_show;
  byId('segBlur').textContent = strings.email_blur;
  byId('segHide').textContent = strings.email_hide;
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
    settingsSection: byId('settingsSection'),
    settingsBtn: byId('settingsBtn'),
    toggleInstall: byId('toggleInstall'),
    emailDisplayControl: byId('emailDisplayControl'),
  };
}

function setProviders(list) {
  providerOrder = list.map((entry) => entry.id);
  providerLabels = {};
  providerInstallTitles = {};
  providers = {};
  list.forEach((entry) => {
    providers[entry.id] = entry.data;
    providerLabels[entry.id] = entry.label;
    providerInstallTitles[entry.id] = entry.install_title;
  });
  if (selectedTab !== 'all' && !providerOrder.includes(selectedTab)) selectedTab = 'all';
}

function buildTabs() {
  const buttons = [{ id: 'all', label: strings.tab_all }].concat(
    providerOrder.map((id) => ({ id, label: providerLabels[id] })),
  );
  nodes.tabs.replaceChildren(...buttons.map((entry) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = entry.id === selectedTab ? 'tab-btn active' : 'tab-btn';
    button.dataset.tab = entry.id;
    button.textContent = entry.label;
    return button;
  }));
}

function bindActions(multipleProviders) {
  byId('closeBtn').addEventListener('click', () => pywebview.api.close());
  byId('changelogLink').addEventListener('click', () => pywebview.api.open_url());
  nodes.settingsBtn.addEventListener('click', toggleSettingsPanel);
  nodes.toggleInstall.addEventListener('change', () => {
    saveSetting('show_install_section', nodes.toggleInstall.checked);
    renderInstallations(getCurrentInstallations());
  });
  nodes.emailDisplayControl.querySelectorAll('.seg-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      saveSetting('email_display', btn.dataset.value);
      renderProfile(getCurrentProfile());
    });
  });
  if (!multipleProviders) return;
  buildTabs();
  nodes.tabs.classList.remove('hidden');
  nodes.tabs.querySelectorAll('.tab-btn').forEach((button) => {
    button.addEventListener('click', () => chooseTab(button.dataset.tab));
  });
}

function toggleSettingsPanel() {
  const visible = nodes.settingsSection.classList.toggle('visible');
  nodes.settingsBtn.classList.toggle('active', visible);
}

function applyPopupSettings(settings) {
  popupSettings = Object.assign({ show_install_section: false, email_display: 'show' }, settings);
  nodes.toggleInstall.checked = popupSettings.show_install_section === true;
  nodes.emailDisplayControl.querySelectorAll('.seg-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.value === popupSettings.email_display);
  });
}

function saveSetting(key, value) {
  popupSettings[key] = value;
  if (key === 'email_display') {
    nodes.emailDisplayControl.querySelectorAll('.seg-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.value === value);
    });
  }
  pywebview.api.save_popup_settings({ [key]: value });
}

let _lastProfile = null;
let _lastInstallations = [];

function getCurrentProfile() { return _lastProfile; }
function getCurrentInstallations() { return _lastInstallations; }

function chooseTab(tab) {
  if (tab === selectedTab) return;
  selectedTab = tab;
  nodes.tabs.querySelectorAll('.tab-btn').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === tab);
  });
  render(currentData());
}

function updateProviders(list) {
  list.forEach((entry) => {
    providers[entry.id] = entry.data;
  });
  render(currentData());
}

function currentData() {
  if (selectedTab === 'all') return combinedData();
  return providers[selectedTab];
}

function combinedData() {
  const lists = providerOrder.map((id) => (providers[id] || {}).usage || []);
  const count = lists.reduce((longest, list) => Math.max(longest, list.length), 0);
  const usage = [];
  for (let index = 0; index < count; index += 1) {
    providerOrder.forEach((id, position) => {
      const entry = lists[position][index];
      if (entry) usage.push({ ...entry, provider: providerLabels[id], label: entry.label });
    });
  }
  const status = providerOrder.reduce(
    (oldest, id) => olderStatus(oldest, (providers[id] || {}).status),
    null,
  );
  return { profile: null, usage, extra: null, installations: [], status };
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
  _lastProfile = profile;
  const visible = !!profile;
  nodes.account.classList.toggle('visible', visible);
  if (!visible) return;
  const emailMode = popupSettings.email_display || 'show';
  const showEmail = !!profile.email && emailMode !== 'hide';
  nodes.emailRow.style.display = showEmail ? '' : 'none';
  if (showEmail) {
    nodes.emailValue.textContent = profile.email;
    nodes.emailValue.classList.toggle('email-blurred', emailMode === 'blur');
  }
  nodes.planValue.textContent = profile.plan || '';
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
  _lastInstallations = items;
  const show = popupSettings.show_install_section === true;
  nodes.install.classList.toggle('visible', show && items.length > 0);
  if (!show || !items.length) return;
  nodes.installTitle.textContent = providerInstallTitles[selectedTab] || strings.claude_code;
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
