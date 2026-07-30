# Changelog

## [Unreleased]

### Added

- **Kimi For Coding support** - when you are signed in to the Kimi Code CLI, Kimi's weekly request quota and 5-hour rate limit appear alongside Claude and Codex: a third tray ring, its own popup tab, a dashboard card with history, and separate alert thresholds. Turn it off with the **Kimi monitoring** toggle in the dashboard settings.
- The local dashboard is now fully translated and follows the same language as the rest of the app across all 13 supported languages.
- The dashboard now shows a connection banner and keeps retrying when it briefly loses contact with the app, instead of silently freezing.
- The tray icon now shows one compact 5-hour usage ring per provider for at-a-glance taskbar monitoring.
- The dashboard settings panel now includes a **Start with Windows** toggle, so autostart can be enabled without opening the tray menu. The change applies immediately, no restart needed.
- Dashboard usage history is now saved to a local file (`agentpulse-history.jsonl`, quota percentages only - never tokens or account data) and survives application restarts. Set `history_persist` to `false` to keep history in memory only.

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
