"""
Claude CLI Tests
==================

Unit tests for claude_cli_path(), find_installations(), refresh_token(), and cli_version().
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from agentpulse import claude_cli
from agentpulse.claude_cli import (
    ClaudeInstallation,
    RefreshResult,
    claude_cli_path,
    cli_version,
    find_installations,
    refresh_token,
)


class _CliPathTestCase(unittest.TestCase):
    """Shared setup: a single mocked CLI candidate and no PATH fallback match.

    ``find_installations()`` and ``refresh_token()`` resolve the CLI through
    ``claude_cli_path()``, which walks ``CLAUDE_CLI_PATHS`` and then falls
    back to a PATH lookup.  Patching the candidate tuple to one mock keeps
    the existing single-path test bodies unchanged; patching ``shutil.which``
    to ``None`` keeps the PATH fallback from interfering.
    """

    def setUp(self):
        self.mock_cli_path = MagicMock()
        path_patcher = patch('agentpulse.claude_cli.CLAUDE_CLI_PATHS', (self.mock_cli_path,))
        path_patcher.start()
        self.addCleanup(path_patcher.stop)
        which_patcher = patch('agentpulse.claude_cli.shutil.which', return_value=None)
        which_patcher.start()
        self.addCleanup(which_patcher.stop)


# ---------------------------------------------------------------------------
# cli_version
# ---------------------------------------------------------------------------

class TestCliVersion(unittest.TestCase):
    """Tests for cli_version()."""

    def setUp(self):
        claude_cli._version_cache.clear()

    @patch('agentpulse.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_parses_version_string(self, _mock_stat, mock_run):
        """Extracts version from '2.1.69 (Claude Code)' output."""
        mock_run.return_value = MagicMock(stdout='2.1.69 (Claude Code)\n', returncode=0)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '2.1.69')

    @patch('agentpulse.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_version_only(self, _mock_stat, mock_run):
        """Handles bare version string without suffix."""
        mock_run.return_value = MagicMock(stdout='3.0.0\n', returncode=0)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '3.0.0')

    @patch('agentpulse.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_empty_output(self, _mock_stat, mock_run):
        """Returns empty string when output is empty."""
        mock_run.return_value = MagicMock(stdout='', returncode=0)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('agentpulse.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_non_version_output(self, _mock_stat, mock_run):
        """Returns empty string for non-version output."""
        mock_run.return_value = MagicMock(stdout='error: something wrong', returncode=1)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('agentpulse.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_timeout_returns_empty(self, _mock_stat, mock_run):
        """Returns empty string on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='claude', timeout=10)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('agentpulse.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_os_error_returns_empty(self, _mock_stat, mock_run):
        """Returns empty string on OSError (binary not found)."""
        mock_run.side_effect = OSError('not found')
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('agentpulse.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_passes_correct_args(self, _mock_stat, mock_run):
        """Calls subprocess with correct arguments."""
        mock_run.return_value = MagicMock(stdout='2.1.69\n', returncode=0)
        path = Path('/fake/claude.exe')
        cli_version(path)
        mock_run.assert_called_once_with(
            [str(path), '--version'],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )

    @patch('agentpulse.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_cache_hit_skips_subprocess(self, _mock_stat, mock_run):
        """Second call with same mtime returns cached version without subprocess."""
        mock_run.return_value = MagicMock(stdout='2.1.69\n', returncode=0)
        path = Path('/fake/claude.exe')
        self.assertEqual(cli_version(path), '2.1.69')
        self.assertEqual(cli_version(path), '2.1.69')
        mock_run.assert_called_once()

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_cache_invalidated_on_mtime_change(self, mock_run):
        """Changed mtime triggers a new subprocess call."""
        mock_run.return_value = MagicMock(stdout='2.1.69\n', returncode=0)
        path = Path('/fake/claude.exe')
        with patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0)):
            self.assertEqual(cli_version(path), '2.1.69')
        mock_run.return_value = MagicMock(stdout='3.0.0\n', returncode=0)
        with patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=2000.0)):
            self.assertEqual(cli_version(path), '3.0.0')
        self.assertEqual(mock_run.call_count, 2)

    def test_stat_failure_returns_empty(self):
        """Returns empty string when stat() fails (file deleted)."""
        with patch('pathlib.Path.stat', side_effect=OSError('not found')):
            self.assertEqual(cli_version(Path('/fake/claude.exe')), '')


# ---------------------------------------------------------------------------
# claude_cli_path
# ---------------------------------------------------------------------------

class TestClaudeCliPath(unittest.TestCase):
    """Tests for claude_cli_path() candidate resolution."""

    def setUp(self):
        self.home_bin = MagicMock(name='home_bin')
        self.npm_shim = MagicMock(name='npm_shim')
        patcher = patch('agentpulse.claude_cli.CLAUDE_CLI_PATHS', (self.home_bin, self.npm_shim))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prefers_home_binary(self):
        self.home_bin.is_file.return_value = True
        self.npm_shim.is_file.return_value = True
        self.assertIs(claude_cli_path(), self.home_bin)

    def test_falls_back_to_npm_install(self):
        self.home_bin.is_file.return_value = False
        self.npm_shim.is_file.return_value = True
        self.assertIs(claude_cli_path(), self.npm_shim)

    def test_unreadable_candidate_does_not_stop_the_search(self):
        self.home_bin.is_file.side_effect = OSError('permission denied')
        self.npm_shim.is_file.return_value = True
        self.assertIs(claude_cli_path(), self.npm_shim)

    @patch('agentpulse.claude_cli.shutil.which', return_value=None)
    def test_none_when_nothing_installed(self, _mock_which):
        self.home_bin.is_file.return_value = False
        self.npm_shim.is_file.return_value = False
        self.assertIsNone(claude_cli_path())

    @patch('agentpulse.claude_cli.shutil.which', return_value='/usr/bin/claude')
    def test_falls_back_to_path_lookup(self, _mock_which):
        """A CLI added to PATH by an installer that uses neither known location is still found."""
        self.home_bin.is_file.return_value = False
        self.npm_shim.is_file.return_value = False
        self.assertEqual(claude_cli_path(), Path('/usr/bin/claude'))


class TestDefaultCliCandidates(unittest.TestCase):
    """Tests for the shipped CLAUDE_CLI_PATHS candidate list (not patched)."""

    def test_home_binary_is_tried_first(self):
        first = claude_cli.CLAUDE_CLI_PATHS[0]
        self.assertEqual(first.name, 'claude.exe')
        self.assertIs(first, claude_cli.CLAUDE_CLI_PATH)

    def test_npm_install_is_the_fallback(self):
        last = claude_cli.CLAUDE_CLI_PATHS[-1]
        self.assertEqual(last.name, 'claude.cmd')
        self.assertEqual(last.parent.name, 'npm')


# ---------------------------------------------------------------------------
# find_installations
# ---------------------------------------------------------------------------

class TestFindInstallations(_CliPathTestCase):
    """Tests for find_installations()."""

    @patch('agentpulse.claude_cli.cli_version', return_value='')
    @patch('agentpulse.claude_cli._EXTENSION_DIRS', [])
    def test_no_installations_found(self, _mock_version):
        """Returns empty list when nothing is installed."""
        self.mock_cli_path.is_file.return_value = False
        result = find_installations()
        self.assertEqual(result, [])

    @patch('agentpulse.claude_cli.cli_version', return_value='2.1.69')
    @patch('agentpulse.claude_cli._EXTENSION_DIRS', [])
    def test_cli_only(self, _mock_version):
        """Returns CLI installation when binary exists."""
        self.mock_cli_path.is_file.return_value = True
        result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'CLI')
        self.assertEqual(result[0].version, '2.1.69')

    @patch('agentpulse.claude_cli.cli_version', return_value='')
    @patch('agentpulse.claude_cli._EXTENSION_DIRS', [])
    def test_cli_exists_but_version_fails(self, _mock_version):
        """CLI binary exists but version command fails - not included."""
        self.mock_cli_path.is_file.return_value = True
        result = find_installations()
        self.assertEqual(result, [])

    @patch('agentpulse.claude_cli.cli_version', return_value='')
    def test_vscode_extension(self, _mock_version):
        """Finds VS Code extension and extracts version from directory name."""
        self.mock_cli_path.is_file.return_value = False
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'anthropic.claude-code-2.1.69-win32-x64').mkdir()
            with patch('agentpulse.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'VS Code')
        self.assertEqual(result[0].version, '2.1.69')

    @patch('agentpulse.claude_cli.cli_version', return_value='')
    def test_picks_highest_version(self, _mock_version):
        """When multiple extension versions exist, picks the highest."""
        self.mock_cli_path.is_file.return_value = False
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'anthropic.claude-code-2.1.63-win32-x64').mkdir()
            (ext_dir / 'anthropic.claude-code-2.1.69-win32-x64').mkdir()
            (ext_dir / 'anthropic.claude-code-2.1.66-win32-x64').mkdir()
            with patch('agentpulse.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].version, '2.1.69')

    @patch('agentpulse.claude_cli.cli_version', return_value='')
    def test_ignores_non_claude_extensions(self, _mock_version):
        """Ignores directories that don't match the Claude extension prefix."""
        self.mock_cli_path.is_file.return_value = False
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'some-other-extension-1.0.0').mkdir()
            (ext_dir / 'anthropic.claude-code-2.1.69-win32-x64').mkdir()
            with patch('agentpulse.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].version, '2.1.69')

    @patch('agentpulse.claude_cli.cli_version', return_value='')
    def test_nonexistent_extension_dir_skipped(self, _mock_version):
        """Extension directories that don't exist are silently skipped."""
        self.mock_cli_path.is_file.return_value = False
        with patch('agentpulse.claude_cli._EXTENSION_DIRS', [('VS Code', Path('/nonexistent'))]):
            result = find_installations()
        self.assertEqual(result, [])

    @patch('agentpulse.claude_cli.cli_version', return_value='2.1.69')
    def test_cli_and_extensions_combined(self, _mock_version):
        """Returns both CLI and extension installations."""
        self.mock_cli_path.is_file.return_value = True
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'anthropic.claude-code-2.1.68-win32-x64').mkdir()
            with patch('agentpulse.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].name, 'CLI')
        self.assertEqual(result[1].name, 'VS Code')


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken(_CliPathTestCase):
    """Tests for refresh_token()."""

    def test_cli_not_found(self):
        """Returns error when CLI binary doesn't exist."""
        self.mock_cli_path.is_file.return_value = False
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'CLI not found')

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_up_to_date(self, mock_run):
        """Parses 'up to date' output correctly."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='Current version: 2.1.69\nChecking for updates to latest version...\nClaude Code is up to date (2.1.69)\n',
            stderr='', returncode=0,
        )
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertFalse(result.updated)
        self.assertEqual(result.old_version, '2.1.69')
        self.assertEqual(result.new_version, '2.1.69')

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_successfully_updated(self, mock_run):
        """Parses 'Successfully updated' output correctly."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='Current version: 2.1.38\nChecking for updates to latest version...\nSuccessfully updated from 2.1.38 to version 2.1.69\n',
            stderr='', returncode=0,
        )
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertTrue(result.updated)
        self.assertEqual(result.old_version, '2.1.38')
        self.assertEqual(result.new_version, '2.1.69')

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_timeout(self, mock_run):
        """Returns error on timeout."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='claude', timeout=60)
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'Timeout')

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_os_error(self, mock_run):
        """Returns error on OSError."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.side_effect = OSError('Permission denied')
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'Permission denied')

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_unexpected_output_success(self, mock_run):
        """Unexpected output with returncode 0 still counts as success."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.return_value = MagicMock(stdout='Something unexpected', stderr='', returncode=0)
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertFalse(result.updated)

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_unexpected_output_failure(self, mock_run):
        """Unexpected output with non-zero returncode is a failure."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.return_value = MagicMock(stdout='Error: something', stderr='', returncode=1)
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertIn('Error: something', result.error)

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_error_message_truncated(self, mock_run):
        """Long error output is truncated to 200 characters."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.return_value = MagicMock(stdout='X' * 300, stderr='', returncode=1)
        result = refresh_token()
        self.assertEqual(len(result.error), 200)

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_update_without_version_prefix(self, mock_run):
        """Parses update output without 'version' prefix before new version."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='updated from 2.1.38 to 2.1.69', stderr='', returncode=0,
        )
        result = refresh_token()
        self.assertTrue(result.updated)
        self.assertEqual(result.old_version, '2.1.38')
        self.assertEqual(result.new_version, '2.1.69')

    @patch('agentpulse.claude_cli.subprocess.run')
    def test_stderr_included_in_parsing(self, mock_run):
        """Output from stderr is also considered when parsing."""
        self.mock_cli_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='', stderr='Claude Code is up to date (2.1.69)', returncode=0,
        )
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertEqual(result.new_version, '2.1.69')


if __name__ == '__main__':
    unittest.main()
