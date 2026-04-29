# Agents Pulse

Monitor Claude and Codex API usage from your Windows system tray. See at a glance how much of your session and weekly quota you've used, get desktop alerts before you run out, and automate actions when quotas reset.

## Features

### Getting started

- **Portable** - a single `.exe` file, no installer. Copy it anywhere and run it.
- **Zero configuration** - works immediately after logging in to Claude Code. No API keys to copy, no settings to fill in.

### Daily visible value

- **Live tray icon** - a compact `AP` mark sits in your taskbar and adapts to your taskbar's light or dark theme.
- **Detail popup** - left-click the icon to see a full breakdown of every quota type (session, weekly, per-model variants, paid overage), the time until each resets, and your account email and plan. Burn-rate predictions show whether you're on pace to use up the quota before it resets.
- **Claude Code versions** - the popup footer shows the Claude Code CLI version and any IDE extension versions (VS Code, Cursor, Windsurf), so you always know what's installed.

### Proactive protection

- **Smart alerts** - Windows desktop notifications fire when you cross configurable thresholds (e.g. 50%, 80%, 95% for the session; 95% for the weekly quota). Time-aware mode suppresses alerts when your pace is still within budget, reducing noise.
- **Quiet hours** - defer all desktop notifications during a configured time window (e.g. overnight) so you aren't woken by alerts.
- **Event commands** - run any shell command automatically when a quota resets or a threshold is crossed. Use this to resume a Claude Code session the moment your session quota refreshes, send a Slack message, or check for app updates. Commands run silently in the background without stealing focus.

### Visual quality

- **Local dashboard** - open a browser dashboard (localhost) from the tray menu to explore usage history across 24h, 7d, or 30d. Includes a burn-rate chart, a heatmap showing which hours of the day you use the most, and a CSV export. The dashboard also has a settings panel for configuring alerts and display options.

### Reliability

- **Automatic token refresh** - when your Claude Code OAuth token expires, the app refreshes it silently via the Claude Code CLI. You never need to restart or re-authenticate manually.
- **Adaptive polling** - polling speeds up automatically when usage is actively increasing and slows down when you're idle or the workstation is locked, keeping network traffic low without missing events.

### Reach and preferences

- **13 languages** - English, German, Spanish, French, Hindi, Indonesian, Italian, Japanese, Korean, Portuguese (Brazil), Ukrainian, Simplified Chinese, Traditional Chinese. Language is auto-detected from your system locale.
- **Codex support** - tracks OpenAI Codex usage alongside Claude when a Codex CLI token is present.
- **Customizable** - adjust polling intervals, alert thresholds, popup colors, which quota fields appear in the icon and tooltip, and more via a JSON settings file or the dashboard settings panel.

## Requirements

- Windows 10 or later
- [Claude Code](https://claude.ai/code) installed and logged in
- [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) (included in Windows 11; available as a free download for Windows 10)

## Installation

1. Download `AgentsPulse.exe` from the [latest release](https://github.com/Waldemarch/AgentsPulse/releases/latest).
2. Place it anywhere you like (next to your projects, in a tools folder, etc.).
3. Double-click to run. The tray icon appears immediately.

No installer, no admin rights required. To start with Windows, right-click the tray icon and enable **Start with Windows**.

## Quick start

1. Log in to Claude Code if you haven't already (`claude login`).
2. Run `AgentsPulse.exe`.
3. Hover over the `AP` tray icon for a quick summary, or left-click for the full popup.
4. To open the dashboard, right-click the tray icon and choose **Open Dashboard**.

## Configuration

All settings work out of the box. To customize behavior, create `agentpulse-settings.json` in the same folder as the `.exe` with only the keys you want to change:

```json
{
  "alert_thresholds_five_hour": [50, 80, 95],
  "poll_interval": 180
}
```

You can also use the **Open Dashboard** → **Settings** panel instead of editing the file manually.

See [docs/configuration.md](docs/configuration.md) for the full list of available settings.

## Docs

- [Configuration reference](docs/configuration.md) - all available settings with defaults and descriptions
- [Event commands](docs/event-commands.md) - automate actions on quota reset or threshold crossing
- [Automatic update check](docs/automatic-update-check.md) - optional PowerShell script to check for new releases via event commands

## Running from source

```bash
git clone https://github.com/Waldemarch/AgentsPulse.git
cd AgentsPulse
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python -m agentpulse
```

To build the standalone EXE:

```bash
python build.py
```

To run the test suite:

```bash
python -m unittest discover -s tests
```

## Privacy and security

- Network traffic is limited to `api.anthropic.com` (Claude usage) and `chatgpt.com` (Codex usage, only when a token is present). No telemetry, no analytics.
- Credentials are read from the Claude Code and Codex CLI login state on your machine and used only in HTTP Authorization headers. They are never logged, stored elsewhere, or transmitted to any other destination.
- The dashboard runs on `localhost` only and is not exposed on the network.
- The app never writes files (it is read-only). Settings are only written when you explicitly save from the dashboard or create the settings file manually.
- All URLs and API endpoints are defined as top-level constants in the source - no dynamic URL construction.
