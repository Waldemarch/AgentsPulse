# Changelog

## [Unreleased]

### Added

- The tray icon now shows compact Claude and Codex 5-hour usage rows for at-a-glance taskbar monitoring.
- The dashboard settings panel now includes a **Start with Windows** toggle, so autostart can be enabled without opening the tray menu. The change applies immediately, no restart needed.

### Changed

- The **Show Claude Code versions** popup setting is now off by default.
- The detail popup now uses thicker quota bars with clearer provider labels when Claude and Codex usage are shown together.
- The dashboard has a modernized look: automatic light/dark theme following the system setting, toggle switches for on/off settings, gradient progress bars with an early warning color at 80%, and refreshed cards, charts, and heatmap.

### Fixed

- Popup settings (e.g. email blur/hide, the Claude Code versions toggle) no longer revert to old values after the popup is closed and reopened.
- Extra-usage amounts now default to the euro symbol (`€`) instead of the system locale's currency, matching how Anthropic bills the credits. Override via `currency_symbol` if Anthropic bills you in a different currency.
