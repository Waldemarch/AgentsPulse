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

Legacy `usage-monitor-settings.json` files are still read as a fallback. To start manually, create an empty file and add keys as needed. You can also use **Open Dashboard** -> **Settings** to create or update the canonical `agentpulse-settings.json` file next to the EXE (or project root when running from source). Settings are read at startup - after editing the file or saving from the dashboard, use the **Restart** option in the tray context menu to apply changes.

## Alert thresholds

Configure usage percentage thresholds that trigger Windows notifications. Session and weekly quotas have separate thresholds since their time horizons differ significantly. Set to an empty array `[]` to disable alerts for a specific quota type.

| Key | Default | Description |
|-----|---------|-------------|
| `alert_thresholds_five_hour` | `[50, 80, 95]` | Thresholds (%) for Session (5hr) |
| `alert_thresholds_seven_day` | `[95]` | Thresholds (%) for Weekly quotas (7 day and all variants) |
| `alert_thresholds_extra_usage` | `[50, 80, 95]` | Thresholds (%) for Extra Usage (paid overage) |
| `alert_time_aware` | `true` | Only alert when usage outpaces elapsed time |
| `alert_time_aware_below` | `90` | Time-aware check applies only to thresholds below this value; thresholds at or above always fire |

Threshold lookup uses a fallback chain: exact match (e.g. `alert_thresholds_seven_day_opus`), then base period (e.g. `alert_thresholds_seven_day`), then no alerts. Provider-specific keys are checked first for non-Claude providers, so Codex can have separate thresholds without changing Claude behavior:

```json
{
    "alert_thresholds_seven_day_opus": [50, 80, 95],
    "alert_thresholds_codex_five_hour": [70, 90],
    "alert_thresholds_codex_seven_day": [90]
}
```

## Tooltip fields

The tray tooltip shows a quick usage summary when you hover over the icon. By default, it displays the session (5h) and weekly (7d) quotas. Use `tooltip_fields` to choose which usage fields appear in the tooltip.

| Key | Default | Description |
|-----|---------|-------------|
| `tooltip_fields` | `["five_hour", "seven_day"]` | Which usage fields to show in the tray tooltip, in order |

Must be an array of non-empty strings. Duplicates are silently removed. An empty array `[]` is valid (tooltip shows only the title, no usage fields). Unknown field names are accepted - if a field is `null` or missing from the API response, it is simply skipped.

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

The tray icon displays the current session (5h) usage as a compact percentage. It always uses the `five_hour` API field; use `tooltip_fields` to choose which fields appear when hovering over the icon.

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

## Local dashboard

Use **Open Dashboard** from the tray context menu to start a browser dashboard on `http://127.0.0.1:8766`. The dashboard stores an in-memory, token-free ring buffer of usage snapshots for up to 30 days (or 12,000 provider snapshots, whichever is reached first). It exposes local-only JSON endpoints for the UI and a CSV export for the selected range.

The dashboard is intentionally not exposed on the network. Usage history is not written to disk. The **Settings** section can save a small allowlisted subset of configuration keys to `agentpulse-settings.json`: Codex enablement, tooltip fields, alert thresholds, predictions, heatmap, quiet hours, and event commands. It does not expose or write OAuth tokens.

Burn-rate and ETA values are calculated locally from the current utilization, reset time, and period length. A healthy pace means the current utilization is at or below the percentage of time elapsed in that quota period.

Prediction and heatmap settings:

| Key | Default | Description |
|-----|---------|-------------|
| `prediction_enabled` | `true` | Show dashboard predictions for projected end-of-day and reset-period utilization |
| `prediction_day_end_time` | `"18:00"` | Local HH:MM time used as the end-of-day prediction target |
| `heatmap_enabled` | `true` | Show a dashboard heatmap of positive usage changes grouped by local hour |

Quiet hours settings:

| Key | Default | Description |
|-----|---------|-------------|
| `quiet_hours_enabled` | `false` | Defer desktop notifications during the configured local time window |
| `quiet_hours_start` | `"22:00"` | Local HH:MM quiet-hours start |
| `quiet_hours_end` | `"08:00"` | Local HH:MM quiet-hours end. Windows that cross midnight are supported |

Event commands still run during quiet hours; only desktop notifications are deferred and deduplicated.

## Language

| Key | Default | Description |
|-----|---------|-------------|
| `language` | *(auto-detected)* | Override the UI language with a language code. Available: `de`, `en`, `es`, `fr`, `hi`, `id`, `it`, `ja`, `ko`, `pt-BR`, `uk`, `zh-CN`, `zh-TW` |

## Currency

The Anthropic API does not include currency information, so the app detects the currency symbol from your Windows locale settings. If your Windows locale currency differs from the currency Anthropic bills you in, you can override just the symbol here. Number formatting (decimal separator, symbol position) always follows your system locale.

| Key | Default | Description |
|-----|---------|-------------|
| `currency_symbol` | *(auto-detected)* | Override the auto-detected currency symbol (e.g., `"$"`, `"€"`, `"¥"`) |

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
| `bar_fg` | `"#4a9eff"` | Progress bar fill |
| `bar_fg_warn` | `"#e05050"` | Progress bar fill when usage outpaces elapsed time, error text |
| `bar_divider` | `"#000c"` | Midnight divider on weekly progress bars |
| `bar_marker` | `"#fffc"` | Time-position marker on progress bars |
