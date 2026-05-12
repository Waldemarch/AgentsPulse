# Changelog

## [Unreleased]

### Added

- The tray icon now shows compact Claude and Codex 5-hour usage rows for at-a-glance taskbar monitoring.

### Changed

- The detail popup now uses thicker quota bars with clearer provider labels when Claude and Codex usage are shown together.

### Fixed

- Extra-usage amounts now default to the euro symbol (`€`) instead of the system locale's currency, matching how Anthropic bills the credits. Override via `currency_symbol` if Anthropic bills you in a different currency.
