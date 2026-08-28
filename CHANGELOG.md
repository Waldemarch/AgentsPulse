# Changelog

## [Unreleased]

### Added

- **Kimi For Coding support** - when you are signed in to the Kimi Code CLI, Kimi's weekly request quota and 5-hour rate limit appear alongside Claude and Codex: a third tray ring, its own popup tab, a dashboard card with history, and separate alert thresholds. Turn it off with the **Kimi monitoring** toggle in the dashboard settings.
- The local dashboard is now fully translated and follows the same language as the rest of the app across all 13 supported languages.
- The dashboard now shows a connection banner and keeps retrying when it briefly loses contact with the app, instead of silently freezing.
- The tray icon now shows one compact 5-hour usage ring per provider for at-a-glance taskbar monitoring.
- The dashboard settings panel now includes a **Start with Windows** toggle, so autostart can be enabled without opening the tray menu. The change applies immediately, no restart needed.
- Dashboard usage history is now saved to a local file (`agentpulse-history.jsonl`, quota percentages only - never tokens or account data) and survives application restarts. Set `history_persist` to `false` to keep history in memory only.
- The dashboard's predictions now include a **pace vs. usual** line per quota, comparing how far along the current session or weekly cycle is against your average pace at the same point in past cycles - so you can tell a heavier-than-usual week from a normal one before it runs out.
- Releases now include a `SHA256SUMS.txt` alongside `AgentsPulse.exe` so the download can be verified.

### Changed

- The dashboard now sends strict security headers (Content-Security-Policy, X-Frame-Options, Referrer-Policy) and no longer reveals its settings file path, and reading the settings now requires the session token - further hardening the local-only dashboard.
- The **Show Claude Code versions** popup setting is now off by default.
- The detail popup now uses thicker quota bars with clearer provider labels when Claude and Codex usage are shown together.
- The dashboard has a modernized look: automatic light/dark theme following the system setting, toggle switches for on/off settings, gradient progress bars with an early warning color at 80%, and refreshed cards, charts, and heatmap.

### Fixed

- Codex usage refreshes now make a single request instead of two back-to-back calls to the same endpoint, lowering the chance of hitting Codex rate limits.
- The dashboard now rejects requests from web pages and forged hostnames: changing settings or firing test events requires a per-run session token and a same-origin browser context, closing a cross-site request forgery hole that could let a malicious website modify event commands. Reopen the dashboard from the tray menu if a saved bookmark stops accepting settings changes.
- The dashboard's 7-day and 30-day history views no longer lose data on restart and can now hold a full 30 days of samples (the previous in-memory buffer filled up after roughly 12 days).
- Popup settings (e.g. email blur/hide, the Claude Code versions toggle) no longer revert to old values after the popup is closed and reopened.
- Extra-usage amounts now default to the euro symbol (`€`) instead of the system locale's currency, matching how Anthropic bills the credits. Override via `currency_symbol` if Anthropic bills you in a different currency.
- The tray tooltip no longer drops Codex or Kimi off the bottom when Claude's own lines already fill the tooltip's 128-character limit - every active provider now gets a compact summary line instead of the ones listed last silently disappearing.
- The app no longer crashes on startup when Codex is logged in with an API key instead of ChatGPT sign-in (or its `auth.json` is otherwise unreadable) - Codex monitoring is now skipped instead of blocking Claude and Kimi monitoring too.
- **Test Reset** and **Test Threshold** (tray menu and dashboard) now set the same `AGENTPULSE_*` environment variables documented in [event-commands.md](docs/event-commands.md) that real reset and threshold events use, including `AGENTPULSE_PROVIDER`. Previously they only set the legacy `USAGE_MONITOR_*` variables, so a command written against the current docs would silently do nothing when tested.
- Claude Code and Codex CLI detection (installed-version display and Claude's automatic token refresh) now also finds installs made via npm or added to PATH, not just the native installer's default location - matching how Kimi Code CLI detection already worked.
- Alert time-awareness settings (`alert_time_aware`, `alert_time_aware_below`) and the tray tooltip's `tooltip_fields` setting now take effect immediately after saving from the dashboard, instead of requiring a restart like the rest of the settings that already applied live.
