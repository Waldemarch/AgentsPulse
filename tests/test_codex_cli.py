"""Tests for codex_cli module - CLI discovery and version reporting."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentpulse import codex_cli

_NPM_SHIM = Path('C:/Users/tester/AppData/Roaming/npm/codex.cmd')


def _proc(stdout: str = 'codex-cli 0.42.0\n', stderr: str = ''):
    return SimpleNamespace(stdout=stdout, stderr=stderr)


class _CliTestCase(unittest.TestCase):
    """Shared setup: a known candidate list and a Windows-only flag stubbed in."""

    def setUp(self):
        codex_cli._version_cache.clear()
        self.addCleanup(codex_cli._version_cache.clear)
        patcher = patch.object(codex_cli, 'CODEX_CLI_PATHS', (_NPM_SHIM,))
        patcher.start()
        self.addCleanup(patcher.stop)
        flag = patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True)
        flag.start()
        self.addCleanup(flag.stop)


class TestCodexCliPath(_CliTestCase):
    """Tests for codex_cli_path() candidate resolution."""

    def test_finds_npm_shim(self):
        with patch.object(Path, 'is_file', lambda self: self == _NPM_SHIM):
            self.assertEqual(codex_cli.codex_cli_path(), _NPM_SHIM)

    @patch.object(codex_cli.shutil, 'which', return_value=None)
    def test_none_when_nothing_installed(self, _mock_which):
        with patch.object(Path, 'is_file', return_value=False):
            self.assertIsNone(codex_cli.codex_cli_path())

    def test_unreadable_candidate_does_not_stop_the_search(self):
        """A PATH-installed binary is still found when the npm shim check errors out."""
        def raising(self):
            raise OSError('permission denied')

        with patch.object(Path, 'is_file', raising), \
             patch.object(codex_cli.shutil, 'which', return_value='C:/Users/tester/.codex/codex.exe'):
            self.assertEqual(codex_cli.codex_cli_path(), Path('C:/Users/tester/.codex/codex.exe'))

    @patch.object(codex_cli.shutil, 'which', return_value='C:/tools/codex.exe')
    def test_falls_back_to_path_lookup(self, _mock_which):
        """The native PowerShell installer's location isn't fixed, but it's on PATH."""
        with patch.object(Path, 'is_file', return_value=False):
            self.assertEqual(codex_cli.codex_cli_path(), Path('C:/tools/codex.exe'))


class TestDefaultCandidates(unittest.TestCase):
    """Tests for the shipped candidate list (not the patched one)."""

    def test_npm_shim_is_the_only_known_location(self):
        self.assertEqual(len(codex_cli.CODEX_CLI_PATHS), 1)
        candidate = codex_cli.CODEX_CLI_PATHS[0]
        self.assertEqual(candidate.name, 'codex.cmd')
        self.assertEqual(candidate.parent.name, 'npm')
        self.assertIs(candidate, codex_cli.CODEX_CLI_PATH)


class TestCodexVersion(_CliTestCase):
    """Tests for codex_version()."""

    @patch.object(codex_cli.shutil, 'which', return_value=None)
    def test_returns_empty_when_not_installed(self, _mock_which):
        with patch.object(Path, 'is_file', return_value=False):
            self.assertEqual(codex_cli.codex_version(), '')

    @patch('agentpulse.codex_cli.subprocess.run')
    @patch.object(Path, 'stat', return_value=MagicMock(st_mtime=1000.0))
    def test_parses_version_string(self, _mock_stat, mock_run):
        mock_run.return_value = _proc('codex-cli 0.42.0\n')
        with patch.object(Path, 'is_file', lambda self: self == _NPM_SHIM):
            self.assertEqual(codex_cli.codex_version(), '0.42.0')

    @patch('agentpulse.codex_cli.subprocess.run')
    @patch.object(Path, 'stat', return_value=MagicMock(st_mtime=1000.0))
    def test_cache_hit_skips_subprocess(self, _mock_stat, mock_run):
        mock_run.return_value = _proc('0.42.0\n')
        with patch.object(Path, 'is_file', lambda self: self == _NPM_SHIM):
            self.assertEqual(codex_cli.codex_version(), '0.42.0')
            self.assertEqual(codex_cli.codex_version(), '0.42.0')
        mock_run.assert_called_once()

    @patch('agentpulse.codex_cli.subprocess.run', side_effect=OSError('not found'))
    @patch.object(Path, 'stat', return_value=MagicMock(st_mtime=1000.0))
    def test_subprocess_error_returns_empty(self, _mock_stat, _mock_run):
        with patch.object(Path, 'is_file', lambda self: self == _NPM_SHIM):
            self.assertEqual(codex_cli.codex_version(), '')


if __name__ == '__main__':
    unittest.main()
