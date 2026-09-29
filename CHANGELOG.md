# Changelog

## [Unreleased]

### Added

- **Kimi For Coding support** - when you are signed in to the Kimi Code CLI, Kimi's weekly request quota and 5-hour rate limit appear alongside Claude and Codex: its own place in the tray icon, its own popup tab, a dashboard card with history, and separate alert thresholds. Turn it off with the **Kimi monitoring** toggle in the dashboard settings.
- The local dashboard is now fully translated and follows the same language as the rest of the app across all 13 supported languages.
- The dashboard now shows a connection banner and keeps retrying when it briefly loses contact with the app, instead of silently freezing.
- The dashboard settings panel now includes a **Start with Windows** toggle, so autostart can be enabled without opening the tray menu. The change applies immediately, no restart needed.
- Dashboard usage history is now saved to a local file (`agentpulse-history.jsonl`, quota percentages only - never tokens or account data) and survives application restarts. Set `history_persist` to `false` to keep history in memory only.
- The dashboard's predictions now include a **pace vs. usual** line per quota, comparing how far along the current session or weekly cycle is against your average pace at the same point in past cycles - so you can tell a heavier-than-usual week from a normal one before it runs out.
- Releases now include a `SHA256SUMS.txt` alongside `AgentsPulse.exe` so the download can be verified.
- **Forecasts** - every quota now shows whether it lasts until its reset - **On track**, **Tight**, or **Limit ~15:47** when it will run out first - in the popup, the tray tooltip, and the dashboard. Sessions are projected from their current pace and weekly limits from your own past weeks, and a quota heading for its limit also names the range it will most likely run out in and how long you would be without quota before the reset.
- **Dashboard 2.0** - the dashboard opens with a one-line summary and a card per provider showing every quota's status, its forecast as a lighter segment of the bar, how much of the window has passed, and the projected usage at your end of day.
- The dashboard's consumption bars show the percentage points each provider used per hour (last 24 hours) or per day, measured on its longest quota so work that counts against several quotas is counted once.
- Dashboard settings open in a slide-out panel, grouped into notifications, automations, tray and providers, and forecasts.
- **Tray icon styles** - the tray icon shows every provider's session usage at a glance as one bar per provider (the default), as rings that fill up and turn solid red at 95%, or as your highest percentage in digits; choose with the `icon_style` setting or in the dashboard's settings, and the change applies immediately.
- While every provider is at its limit, the tray icon counts down to the reset, then shows a green check mark for ten minutes once you can work again.
- The popup has **Dashboard** and **Refresh** buttons and marks every provider with its own color.
- **Claude Code status line** - turn it on in the dashboard settings and paste the entry shown there into `~/.claude/settings.json` to see every provider's session and weekly usage, the session reset time, and a colored warning for tight quotas or limits right below the Claude Code prompt.
- **While you were away** - after locking the workstation or leaving it idle for at least 15 minutes, you get one notification that sums up which quotas reset and how much each provider used in the meantime, instead of the separate alerts held back during your absence.
- **Daily budget** - the popup and the dashboard show how much of each weekly quota you can use today and still have it last until the reset: what is left, spread over today and your remaining workdays (`budget_workdays`, Monday to Friday by default).
- **This week vs your typical week** - the dashboard draws the current week against your previous weeks and their median, with the forecast that follows your usual rhythm and the range of your past weeks, so a heavier week than usual stands out at a glance.

### Changed

- The dashboard now sends strict security headers (Content-Security-Policy, X-Frame-Options, Referrer-Policy) and no longer reveals its settings file path, and reading the settings now requires the session token - further hardening the local-only dashboard.
- The **Show Claude Code versions** popup setting is now off by default.
- The detail popup now uses thicker quota bars with clearer provider labels when Claude and Codex usage are shown together.
- The dashboard has a modernized look: automatic light/dark theme following the system setting, toggle switches for on/off settings, and refreshed cards, charts, and heatmap.
- The dashboard's usage history is split into one panel per quota period (session, weekly) on a shared time axis, with a crosshair tooltip for every series (also by keyboard), legend toggles, the latest value next to each line, and a data table with the highest reading per hour or day; every chart uses one color per provider that stays distinguishable for color-blind users in both themes.
- The usage heatmap shows every weekday by hour for one provider at a time, averaged over the last four weeks, and names the busiest hour.
- The popup, the tray tooltip, and the dashboard no longer warn in red whenever usage is ahead of the elapsed time; a warning now means the forecast ends tight or runs out before the reset.
- The dashboard's forecasts, end-of-day projection, and pace comparison moved from a separate Predictions section onto each provider's card, and the diagnostics became a line at the bottom of the page.
- The dashboard loads 7- and 30-day history aggregated to the highest reading per 10 or 30 minutes (the CSV export still has every sample), downloads history only after a new reading, and pauses while its tab is in the background, instead of downloading every sample again every 15 seconds.
- Saving settings in the dashboard now simply confirms the save and asks for a restart only when Codex or Kimi monitoring was switched on or off; the message no longer shows the settings file's location.

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
- Dashboard charts no longer grow taller on every refresh when Windows display scaling is above 100% (for example 125% or 150%).
- The dashboard's usage history now includes every quota the API reports, such as the per-model weekly limits, instead of only the session and weekly quotas.
- Chart lines no longer run diagonally across hours without readings (overnight, while the PC was locked) or across a quota reset.
- Chart axis labels no longer overlap, and values outside a chart's range no longer spill outside it.
- The dashboard's end-of-day prediction no longer projects a 5-hour session past its reset (a window that resets before the target time shows its projection at reset instead), and predictions stop at 100% instead of showing values such as 999%.
- The usage heatmap no longer counts the same work several times by adding up the session, weekly, and per-model quotas; it now uses each provider's weekly quota, color, and scale.
- Saving settings from the dashboard no longer merges several event commands into one `&&` chain that stops at the first failing command; each command is edited on its own line and still runs independently.
- The dashboard's script and styles are always served with the correct content type instead of the one registered in Windows, which could leave the dashboard blank on systems where other software registered `.js` files as plain text.

### Removed

- The dashboard's burn-rate chart - the consumption bars show how much of each quota was used per hour or day instead.
