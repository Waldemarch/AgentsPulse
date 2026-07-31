"""Tests for kimi_cli module - CLI discovery and version reporting."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentpulse import kimi_cli

_BIN_PATH = Path('C:/Users/tester/.kimi-code/bin/kimi.exe')
_NPM_PATH = Path('C:/Users/tester/AppData/Roaming/npm/kimi.cmd')


def _only(*present: Path):
    """Report the given paths as installed and every other path as missing."""
    installed = set(present)
    return lambda self: self in installed


def _proc(stdout: str = 'kimi 2.0.1\n', stderr: str = ''):
    return SimpleNamespace(stdout=stdout, stderr=stderr)


class _CliTestCase(unittest.TestCase):
    """Shared setup: a known candidate list and a Windows-only flag stubbed in."""

    def setUp(self):
        kimi_cli._version_cache.clear()
        self.addCleanup(kimi_cli._version_cache.clear)
        patcher = patch.object(kimi_cli, 'KIMI_CLI_PATHS', (_BIN_PATH, _NPM_PATH))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Only defined on Windows; the module reads it when spawning the CLI.
        flag = patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True)
        flag.start()
        self.addCleanup(flag.stop)


class TestKimiCliPath(_CliTestCase):
    """Tests for kimi_cli_path() candidate resolution."""

    def test_prefers_home_binary(self):
        with patch.object(Path, 'is_file', _only(_BIN_PATH, _NPM_PATH)):
            self.assertEqual(kimi_cli.kimi_cli_path(), _BIN_PATH)

    def test_falls_back_to_npm_install(self):
        with patch.object(Path, 'is_file', _only(_NPM_PATH)):
            self.assertEqual(kimi_cli.kimi_cli_path(), _NPM_PATH)

    def test_none_when_nothing_installed(self):
        with patch.object(Path, 'is_file', _only()):
            self.assertIsNone(kimi_cli.kimi_cli_path())

    def test_unreadable_candidate_does_not_stop_the_search(self):
        def raising(self):
            if self == _BIN_PATH:
                raise OSError('permission denied')
            return self == _NPM_PATH

        with patch.object(Path, 'is_file', raising):
            self.assertEqual(kimi_cli.kimi_cli_path(), _NPM_PATH)


class TestDefaultCandidates(unittest.TestCase):
    """Tests for the shipped candidate list (not the patched one)."""

    def test_home_binary_is_tried_first(self):
        """The preferred candidate lives under the configured CLI home directory."""
        first = kimi_cli.KIMI_CLI_PATHS[0]
        self.assertEqual(first.name, 'kimi.exe')
        self.assertEqual(first.parent.name, 'bin')
        self.assertEqual(first.parent.parent, kimi_cli.KIMI_CODE_HOME)

    def test_npm_install_is_the_fallback(self):
        last = kimi_cli.KIMI_CLI_PATHS[-1]
        self.assertEqual(last.name, 'kimi.cmd')
        self.assertEqual(last.parent.name, 'npm')


class TestKimiVersion(_CliTestCase):
    """Tests for kimi_version()."""

    def test_parses_version_from_stdout(self):
        with patch.object(Path, 'is_file', _only(_BIN_PATH)), \
             patch.object(Path, 'stat', lambda self: SimpleNamespace(st_mtime=1000.0)), \
             patch.object(subprocess, 'run', return_value=_proc()):
            self.assertEqual(kimi_cli.kimi_version(), '2.0.1')

    def test_parses_version_from_stderr(self):
        with patch.object(Path, 'is_file', _only(_BIN_PATH)), \
             patch.object(Path, 'stat', lambda self: SimpleNamespace(st_mtime=1000.0)), \
             patch.object(subprocess, 'run', return_value=_proc(stdout='', stderr='kimi 1.4.9')):
            self.assertEqual(kimi_cli.kimi_version(), '1.4.9')

    def test_runs_the_resolved_binary(self):
        with patch.object(Path, 'is_file', _only(_NPM_PATH)), \
             patch.object(Path, 'stat', lambda self: SimpleNamespace(st_mtime=1000.0)), \
             patch.object(subprocess, 'run', return_value=_proc()) as mock_run:
            kimi_cli.kimi_version()
        self.assertEqual(mock_run.call_args[0][0], [str(_NPM_PATH), '--version'])

    def test_empty_when_no_cli_installed(self):
        with patch.object(Path, 'is_file', _only()), \
             patch.object(subprocess, 'run') as mock_run:
            self.assertEqual(kimi_cli.kimi_version(), '')
        mock_run.assert_not_called()

    def test_empty_when_output_has_no_version(self):
        with patch.object(Path, 'is_file', _only(_BIN_PATH)), \
             patch.object(Path, 'stat', lambda self: SimpleNamespace(st_mtime=1000.0)), \
             patch.object(subprocess, 'run', return_value=_proc(stdout='no version here')):
            self.assertEqual(kimi_cli.kimi_version(), '')

    def test_empty_when_the_binary_fails_to_run(self):
        with patch.object(Path, 'is_file', _only(_BIN_PATH)), \
             patch.object(Path, 'stat', lambda self: SimpleNamespace(st_mtime=1000.0)), \
             patch.object(subprocess, 'run', side_effect=OSError('boom')):
            self.assertEqual(kimi_cli.kimi_version(), '')

    def test_cache_hit_skips_the_subprocess(self):
        with patch.object(Path, 'is_file', _only(_BIN_PATH)), \
             patch.object(Path, 'stat', lambda self: SimpleNamespace(st_mtime=1000.0)), \
             patch.object(subprocess, 'run', return_value=_proc()) as mock_run:
            kimi_cli.kimi_version()
            kimi_cli.kimi_version()
        mock_run.assert_called_once()

    def test_cache_invalidated_when_the_binary_changes(self):
        """An update rewrites kimi.exe, so a new mtime must re-read the version."""
        mtimes = iter([1000.0, 2000.0])
        with patch.object(Path, 'is_file', _only(_BIN_PATH)), \
             patch.object(Path, 'stat', lambda self: SimpleNamespace(st_mtime=next(mtimes))), \
             patch.object(subprocess, 'run', side_effect=[_proc(), _proc('kimi 2.1.0')]) as mock_run:
            first = kimi_cli.kimi_version()
            second = kimi_cli.kimi_version()
        self.assertEqual((first, second), ('2.0.1', '2.1.0'))
        self.assertEqual(mock_run.call_count, 2)

    def test_cache_is_keyed_by_path(self):
        """A version cached for one install is not reused for another."""
        with patch.object(Path, 'stat', lambda self: SimpleNamespace(st_mtime=1000.0)), \
             patch.object(subprocess, 'run', side_effect=[_proc(), _proc('kimi 3.3.3')]) as mock_run:
            with patch.object(Path, 'is_file', _only(_BIN_PATH)):
                from_home = kimi_cli.kimi_version()
            with patch.object(Path, 'is_file', _only(_NPM_PATH)):
                from_npm = kimi_cli.kimi_version()
        self.assertEqual((from_home, from_npm), ('2.0.1', '3.3.3'))
        self.assertEqual(mock_run.call_count, 2)


if __name__ == '__main__':
    unittest.main()
