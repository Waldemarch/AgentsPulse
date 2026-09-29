# Configuration

All settings work out of the box - no configuration file is needed. To customize behavior, create a file called `agentpulse-settings.json` with only the keys you want to change:

```json
{
  "poll_interval": 180,
  "bar_fg": "#00cc66",
  "bar_fg_warn": "#ff6600"
}
```

The app searches for this file in these locations (first match wins):

1. **Next to the EXE** (or project root when running from source)
2. **`$CLAUDE_CONFIG_DIR/agentpulse-settings.json`** (only if `CLAUDE_CONFIG_DIR` is set and differs from `~/.claude/`)
3. **`~/.claude/agentpulse-settings.json`**

Legacy `usage-monitor-settings.json` files are still read as a fallback. To start manually, create an empty file and add keys as needed. You can also use **Open Dashboard** -> **Settings** to create or update the canonical `agentpulse-settings.json` file next to the EXE (or project root when running from source). Settings are read at startup - after editing the file by hand, use the **Restart** option in the tray context menu to apply changes. Settings saved from the dashboard apply immediately, except switching Codex or Kimi monitoring on or off, which takes effect after a restart (the dashboard says so when you save).

## Alert thresholds

Configure usage percentage thresholds that trigger Windows notifications. Session and weekly quotas have separate thresholds since their time horizons differ significantly. Set to an empty array `[]` to disable alerts for a specific quota type.

| Key | Default | Description |
|-----|---------|-------------|
| `alert_thresholds_five_hour` | `[50, 80, 95]` | Thresholds (%) for Session (5hr) |
| `alert_thresholds_seven_day` | `[95]` | Thresholds (%) for Weekly quotas (7 day and all variants) |
| `alert_thresholds_extra_usage` | `[50, 80, 95]` | Thresholds (%) for Extra Usage (paid overage) |
| `alert_time_aware` | `true` | Only alert when usage outpaces elapsed time |
| `alert_time_aware_below` | `90` | Time-aware check applies only to thresholds below this value; thresholds at or above always fire |

Threshold lookup uses a fallback chain: exact match (e.g. `alert_thresholds_seven_day_opus`), then base period (e.g. `alert_thresholds_seven_day`), then no alerts. Provider-specific keys are checked first for non-Claude providers, so Codex and Kimi can have separate thresholds without changing Claude behavior:

```json
{
    "alert_thresholds_seven_day_opus": [50, 80, 95],
    "alert_thresholds_codex_five_hour": [70, 90],
    "alert_thresholds_codex_seven_day": [90],
    "alert_thresholds_kimi_five_hour": [70, 90],
    "alert_thresholds_kimi_seven_day": [90]
}
```

## While you were away

When you lock the workstation or leave it idle for `idle_pause` seconds, the app holds back its desktop notifications. Come back after at least 15 minutes, and it reads every provider's usage right away and shows one notification that sums up your absence instead of the reset and threshold alerts held back meanwhile:

```text
While you were away (1h 35m)
Claude: 5h available again since 12:30, now 12%, 7d 88% → 90%
Codex: 5h 10% → 35%
```

Each line names the quotas of one provider that reset while you were away (with the time they reset) or grew by at least one percentage point, sessions first. Providers without changes are left out, and without any change there is no notification. After a shorter absence, the held-back alerts are shown one by one as before. Notifications that are not about a quota window, such as an account switch or an extra-usage alert, still appear on their own, and so do alerts that were already waiting for quiet hours to end before you left. During quiet hours, the summary waits like every other notification.

| Key | Default | Description |
|-----|---------|-------------|
| `away_summary_enabled` | `true` | Sum up what happened while you were away in one notification when you come back. Also available in the dashboard's settings panel |

## Tooltip fields

The tray tooltip shows a quick usage summary when you hover over the icon. By default, it displays the session (5h) and weekly (7d) quotas. Use `tooltip_fields` to choose which usage fields appear in the tooltip and in the [Claude Code status line](#claude-code-status-line).

| Key | Default | Description |
|-----|---------|-------------|
| `tooltip_fields` | `["five_hour", "seven_day"]` | Which usage fields to show in the tray tooltip, in order |

Must be an array of non-empty strings. Duplicates are silently removed. An empty array `[]` is valid (tooltip shows only the title, no usage fields). Unknown field names are accepted - if a field is `null` or missing from the API response, it is simply skipped.

Windows caps tray tooltips at 128 characters and whole trailing lines are dropped to fit, so with several providers active the last provider's section may not be visible. Shorten `tooltip_fields` if you want every provider in the tooltip.

**Known field names:** `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`

**Example** - show session and Sonnet quota in the tooltip:

```json
{
    "tooltip_fields": ["five_hour", "seven_day_sonnet"]
}
```

## Popup fields

The popup shows usage bars for all active quota types by default. Use `popup_fields` to control which bars appear and in what order.

| Key | Default | Description |
|-----|---------|-------------|
| `popup_fields` | `["*"]` | Which usage fields to show in the popup, in order. `"*"` is a wildcard meaning "all remaining non-null fields in default order" |

Must be an array of non-empty strings. `"*"` may appear at most once. Duplicates are silently removed. Unknown field names are accepted - if a field is `null` or missing from the API response, it is simply skipped.

**Known field names:** `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`

**Default order** (used for `"*"` and when no setting is present): shorter periods first (`hour` before `day`), base field before variants, variants alphabetically.

**Examples:**

| Setting | Result |
|---------|--------|
| *(not set)* | All non-null fields in default order |
| `["five_hour", "seven_day_sonnet", "*"]` | Session first, then Sonnet, then all remaining |
| `["five_hour", "seven_day"]` | Only these two, everything else hidden |
| `["*"]` | Same as not set |

```json
{
    "popup_fields": ["five_hour", "seven_day_sonnet", "*"]
}
```

## Tray icon

The tray icon shows each provider's session usage - its shortest quota window, such as the 5-hour session - in the order of the popup's tabs. Usage turns orange at 80% and red at 95%. Use `tooltip_fields` to choose which usage fields appear when hovering over the icon.

| Key | Default | Description |
|-----|---------|-------------|
| `icon_style` | `"bars"` | `"bars"`: one vertical bar per provider, filled from the bottom. `"rings"`: one concentric ring per provider, the used share drawn clockwise from 12 o'clock; a ring at 95% or more turns solid red. `"number"`: the highest percentage as digits over its meter (`!` once a limit is reached). Also available in the dashboard's settings panel, where a change applies immediately |

When every provider shown in the icon has reached a limit, the icon switches to a red countdown to the moment the first of them can be used again - minutes below an hour (`47`), then hours (`5h`) or days (`2d`). When that countdown ends, a green check mark shows for ten minutes.

## Event commands

Run a shell command when a usage event occurs. See [Event Commands](event-commands.md) for examples and available environment variables.

| Key | Default | Description |
|-----|---------|-------------|
| `on_reset_command` | *(none)* | Shell command (or array of commands) to run when a quota resets (usage drops) |
| `on_threshold_command` | *(none)* | Shell command (or array of commands) to run when usage crosses a configured alert threshold |

## Polling intervals

| Key | Default | Description |
|-----|---------|-------------|
| `poll_interval` | `180` | Seconds between API updates |
| `poll_fast` | `120` | Seconds when usage is actively increasing |
| `poll_fast_extra` | `2` | Extra fast polls after usage stops increasing |
| `poll_error` | `30` | Seconds after a transient error (5xx, network). Rate-limit errors (429) use exponential backoff instead |
| `max_backoff` | `900` | Maximum backoff in seconds for rate-limit errors (15 min) |
| `idle_pause` | `300` | Seconds of inactivity before polling pauses (0 = disable). Polling also pauses when the workstation is locked |

## Providers

| Key | Default | Description |
|-----|---------|-------------|
| `codex_enabled` | `true` | Enable Codex usage monitoring when a local Codex CLI token is present. If no token exists in `~/.codex/auth.json` (or `CODEX_CONFIG_DIR/auth.json`), Codex UI is hidden and no Codex usage request is made |
| `kimi_enabled` | `true` | Enable Kimi usage monitoring when a local Kimi Code CLI token is present. If no token exists in `~/.kimi-code/credentials/kimi-code.json` (or `KIMI_CODE_HOME/credentials/kimi-code.json`), Kimi UI is hidden and no Kimi usage request is made |

Kimi credentials are read only. The app never uses the refresh token and never rewrites the credential file, so an expired Kimi session is renewed by signing in with the Kimi Code CLI again.

The installed Kimi Code CLI version shown in the popup and dashboard is read from the first of these that exists: `~/.kimi-code/bin/kimi.exe` (or `KIMI_CODE_HOME/bin/kimi.exe`), then `%APPDATA%\npm\kimi.cmd` for npm installs. A missing CLI only hides the version row - usage monitoring itself needs the credential file, not the binary.

### Kimi quota mapping

The Kimi Code API reports a weekly request allowance and a set of rolling rate-limit windows. They are mapped onto the same field names the other providers use, so tooltip fields, popup fields, and alert thresholds work identically:

| Kimi API | Field | Notes |
|----------|-------|-------|
| `usage` | `seven_day` | Weekly request quota; refreshes every 7 days from your subscription date, not on a fixed weekday |
| `limits[]` with a 300 minute window | `five_hour` | Rolling 5-hour rate limit; 200 requests on every membership tier |

Windows the app cannot name in whole hours or days are skipped rather than shown under an invented field name.

Kimi's membership also has a monthly credit pool that can freeze Kimi Code once it is exhausted, independent of the weekly quota. The Kimi Code API does not expose it, so the app cannot show it - check it on your Kimi membership page.

## Local dashboard

Use **Open Dashboard** from the tray context menu or the popup's **Dashboard** button to start a browser dashboard on `http://127.0.0.1:8766`. The dashboard keeps a token-free ring buffer of usage snapshots for up to 30 days (or 60,000 provider snapshots, whichever is reached first). It exposes local-only JSON endpoints for the UI and a CSV export for the selected range. The 7-day and 30-day charts receive the history aggregated to the highest reading per 10 or 30 minutes, which keeps limit hits visible; the CSV export always contains every sample. The dashboard downloads history again only after a new reading arrives and pauses while its browser tab is in the background.

Usage history is persisted to `agentpulse-history.jsonl` next to the executable (only quota percentages, reset timestamps, and error messages - never tokens, emails, or account identifiers), so charts and the heatmap survive application restarts. Set `history_persist` to `false` to keep history in memory only; the file can be deleted at any time.

The dashboard is intentionally not exposed on the network, and requests are validated beyond the localhost bind: the `Host` header must be a loopback host (blocks DNS rebinding), and every POST endpoint requires a random per-run session token plus a same-origin `Origin` header (blocks cross-site request forgery from web pages). The token is embedded in the URL when the dashboard is opened from the tray menu; if a saved bookmark stops accepting settings changes, reopen the dashboard from the tray menu. The **Settings** panel (the **Settings** button in the header) can save a small allowlisted subset of configuration keys to `agentpulse-settings.json`: Codex and Kimi enablement, the tray icon style, tooltip fields, alert thresholds, the away summary, predictions, heatmap, the Claude Code status line, quiet hours, and event commands (one command per line, saved as an array - each command runs on its own). It does not expose or write OAuth tokens, and it never shows the settings file's path.

History settings:

| Key | Default | Description |
|-----|---------|-------------|
| `history_persist` | `true` | Persist dashboard usage history to `agentpulse-history.jsonl` so it survives restarts. Set to `false` for in-memory history only |

The dashboard opens with a one-line summary and a card per provider. Every quota shows its status, its usage with a lighter segment projecting it to the reset, and a marker for how much of the window has passed. Below them, usage history is drawn in one panel per window length (session, weekly) on a shared time axis: hover or focus the chart and use the arrow keys to read every series at one moment, toggle series in the legend, or open the data table for the highest reading per hour (last 24 hours) or per day. Consumption bars show the percentage points each provider used per hour or day, measured on its longest quota window so work that counts against several quotas is counted once, and the heatmap shows the average use per weekday and hour over the last four weeks.

Forecasts are calculated locally from the usage history:

- **Session windows** (measured in hours) are projected from their pace: 60% the change over the last half hour, 40% the window's average (counted over at least its first ten minutes).
- **Multi-day windows** (weekly limits) follow your own rhythm: the median usage that past cycles added after the same point of their window, once history holds such cycles. Before that, the average pace over at least one full day is used, so a single working session is not extrapolated across nights and weekends.

The status follows the forecast at the reset: **On track** below 90%, **Tight** from 90%, **Limit before reset** (or **Limit ~15:47** when a pace gives the time) at 100%, and **Limit reached** once a quota is used up. The popup, the tray tooltip, and the dashboard show the same status.

Prediction and heatmap settings:

| Key | Default | Description |
|-----|---------|-------------|
| `prediction_enabled` | `true` | Show forecasts: the projected usage at each reset in the dashboard and popup, and statuses such as **Tight** or **Limit ~15:47** in the dashboard, popup, and tooltip. When off, only a reached limit is flagged |
| `prediction_day_end_time` | `"18:00"` | Local HH:MM end of your day: quotas that reset later also show their projected usage at this time in the dashboard (tomorrow's, once today's has passed) |
| `heatmap_enabled` | `true` | Show the dashboard's weekday-by-hour heatmap |

Quiet hours settings:

| Key | Default | Description |
|-----|---------|-------------|
| `quiet_hours_enabled` | `false` | Defer desktop notifications during the configured local time window |
| `quiet_hours_start` | `"22:00"` | Local HH:MM quiet-hours start |
| `quiet_hours_end` | `"08:00"` | Local HH:MM quiet-hours end. Windows that cross midnight are supported |

Event commands still run during quiet hours; only desktop notifications are deferred and deduplicated.

## Claude Code status line

Your quotas can appear in the status line below the Claude Code prompt:

```text
Claude 5h 42% ↺14:30 · 7d 61% | Codex 5h 10% ↺16:05 · 7d 3%
```

1. Turn on **Show usage in the Claude Code status line** in the dashboard's **Settings** panel, or set `statusline_enabled` to `true`. The local dashboard server then starts together with the app.
2. Copy the entry the panel shows into `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "curl.exe -sf --max-time 1 http://127.0.0.1:8766/api/statusline"
  }
}
```

The panel's entry uses the port the server actually runs on: `8766`, or the next free port when that one is taken. `curl.exe` ships with Windows 10 (version 1803 and later) and Windows 11; the `.exe` keeps the command working in Windows PowerShell, where `curl` means `Invoke-WebRequest`.

The line shows the tray tooltip's quota fields (`tooltip_fields`) for every active provider. Session windows add the local time of their reset (`↺14:30`). A **Tight** quota turns yellow; a quota heading for its limit or at its limit turns red and names its status, such as **Limit ~13:55**, or **Limit reached** with the time it resets. The text comes from the app's latest reading, so Claude Code's frequent refreshes never cause an API request. While the app is not running, `curl.exe` prints nothing and the status line stays empty.

Query parameters change what the line shows (quote the URL inside the command when you add one):

| Parameter | Effect |
|-----------|--------|
| `provider=claude` | Only this provider: `claude`, `codex`, or `kimi` |
| `color=0` | Plain text without ANSI color codes |

For example: `"command": "curl.exe -sf --max-time 1 \"http://127.0.0.1:8766/api/statusline?provider=claude\""`.

`/api/statusline` is read-only and follows the dashboard's local-only rules: it answers only loopback clients with a loopback `Host` header, and only while the status line is turned on. Like the dashboard's status data it needs no session token, and it sends no CORS headers, so web pages cannot read it.

| Key | Default | Description |
|-----|---------|-------------|
| `statusline_enabled` | `false` | Serve the Claude Code status line at `/api/statusline`; the local dashboard server starts together with the app. Also available in the dashboard's settings panel |

## Language

| Key | Default | Description |
|-----|---------|-------------|
| `language` | *(auto-detected)* | Override the UI language with a language code. Available: `de`, `en`, `es`, `fr`, `hi`, `id`, `it`, `ja`, `ko`, `pt-BR`, `uk`, `zh-CN`, `zh-TW` |

## Currency

The Anthropic API does not include currency information, so the app defaults to the euro symbol (`€`) for the extra-usage amounts. If Anthropic bills you in a different currency, override the symbol here. Number formatting (decimal separator, symbol position) always follows your system locale.

| Key | Default | Description |
|-----|---------|-------------|
| `currency_symbol` | `"€"` | Override the currency symbol shown for extra-usage spend (e.g., `"$"`, `"€"`, `"¥"`) |

## Tray icon colors

Override individual channels as RGBA arrays `[R, G, B, A]` (0-255). Unspecified keys keep their defaults.

| Key | Default | Description |
|-----|---------|-------------|
| `icon_light` | `{"fg": [255,255,255,255], "fg_half": [255,255,255,80], "fg_dim": [255,255,255,140]}` | Light icons for dark taskbar |
| `icon_dark` | `{"fg": [0,0,0,255], "fg_half": [0,0,0,80], "fg_dim": [0,0,0,140]}` | Dark icons for light taskbar |

## Popup colors

| Key | Default | Description |
|-----|---------|-------------|
| `bg` | `"#1e1e1e"` | Background |
| `fg` | `"#cccccc"` | Text |
| `fg_dim` | `"#888888"` | Dimmed text (labels, reset times) |
| `fg_heading` | `"#ffffff"` | Section headings |
| `fg_link` | `"#4a9eff"` | Link text (e.g. changelog) |
| `bar_bg` | `"#333333"` | Progress bar background |
| `bar_fg` | `"#4a9eff"` | Progress bar fill while a quota is on track |
| `bar_fg_tight` | `"#e8b339"` | Progress bar fill and status when a quota is projected to end within ten points of its limit |
| `bar_fg_warn` | `"#e05050"` | Progress bar fill and status when a quota is projected to run out before it resets or has run out, error text |
| `bar_divider` | `"#000c"` | Midnight divider on weekly progress bars |
| `bar_marker` | `"#fffc"` | Time-position marker on progress bars |
