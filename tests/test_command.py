"""
Command Tests
===============

Unit tests for the command module: subprocess execution with environment variables.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agentpulse.command import run_event_command


class TestRunEventCommand(unittest.TestCase):
    """Tests for run_event_command() subprocess launching."""

    @patch('agentpulse.command.subprocess.Popen')
    def test_command_executed_with_shell(self, mock_popen: MagicMock):
        """Command is passed to Popen with shell=True."""
        run_event_command(['echo hello'], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], 'echo hello')
        self.assertTrue(kwargs['shell'])

    @patch('agentpulse.command.subprocess.Popen')
    def test_env_vars_merged_into_environment(self, mock_popen: MagicMock):
        """Event-specific variables are merged into the process environment."""
        env_vars = {
            'USAGE_MONITOR_EVENT': 'threshold',
            'USAGE_MONITOR_VARIANT': 'five_hour',
            'USAGE_MONITOR_UTILIZATION': '84.5',
        }
        run_event_command(['notify.bat'], env_vars)

        passed_env = mock_popen.call_args[1]['env']
        for key, value in env_vars.items():
            self.assertEqual(passed_env[key], value)

    @patch('agentpulse.command.subprocess.Popen')
    def test_version_env_var_always_present(self, mock_popen: MagicMock):
        """Version env vars are always set from __version__."""
        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        passed_env = mock_popen.call_args[1]['env']
        from agentpulse import __version__
        self.assertEqual(passed_env['AGENTPULSE_VERSION'], __version__)
        self.assertEqual(passed_env['USAGE_MONITOR_VERSION'], __version__)

    @patch('agentpulse.command.subprocess.Popen')
    def test_existing_env_preserved(self, mock_popen: MagicMock):
        """Existing environment variables are preserved alongside new ones."""
        with patch.dict('os.environ', {'PATH': '/usr/bin', 'HOME': '/home/user'}):
            run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        passed_env = mock_popen.call_args[1]['env']
        self.assertEqual(passed_env['PATH'], '/usr/bin')
        self.assertEqual(passed_env['HOME'], '/home/user')
        self.assertEqual(passed_env['USAGE_MONITOR_EVENT'], 'reset')

    @patch('agentpulse.command.subprocess.Popen')
    def test_stdout_stderr_devnull(self, mock_popen: MagicMock):
        """stdout and stderr are redirected to DEVNULL."""
        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        kwargs = mock_popen.call_args[1]
        self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)

    @patch('agentpulse.command.subprocess.Popen')
    def test_create_no_window_flag(self, mock_popen: MagicMock):
        """CREATE_NO_WINDOW flag is set."""
        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        kwargs = mock_popen.call_args[1]
        self.assertEqual(kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    @patch('agentpulse.command.subprocess.Popen')
    def test_empty_list_skipped(self, mock_popen: MagicMock):
        """Empty command list does not invoke Popen."""
        run_event_command([], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_popen.assert_not_called()

    @patch('agentpulse.command.traceback.print_exc')
    @patch('agentpulse.command.subprocess.Popen', side_effect=OSError('not found'))
    def test_popen_exception_caught(self, mock_popen: MagicMock, mock_print_exc: MagicMock):
        """OSError from Popen is caught and printed to stderr."""
        run_event_command(['nonexistent_command'], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_print_exc.assert_called_once()

    @patch('agentpulse.command.traceback.print_exc')
    @patch('agentpulse.command.subprocess.Popen', side_effect=ValueError('bad'))
    def test_unexpected_exception_caught(self, mock_popen: MagicMock, mock_print_exc: MagicMock):
        """Unexpected exceptions from Popen are caught and printed to stderr."""
        run_event_command(['bad_command'], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_print_exc.assert_called_once()

    @patch('agentpulse.command.subprocess.Popen')
    def test_popen_not_waited(self, mock_popen: MagicMock):
        """Popen result is not waited on (fire-and-forget)."""
        run_event_command(['long_running'], {'USAGE_MONITOR_EVENT': 'reset'})

        mock_process = mock_popen.return_value
        mock_process.wait.assert_not_called()
        mock_process.communicate.assert_not_called()

    @patch('agentpulse.command.subprocess.Popen')
    def test_cwd_set_to_project_root(self, mock_popen: MagicMock):
        """Working directory is set to the project root (non-frozen)."""
        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        kwargs = mock_popen.call_args[1]
        expected = Path(__file__).resolve().parent.parent
        self.assertEqual(kwargs['cwd'], expected)

    @patch('agentpulse.command.subprocess.Popen')
    @patch('agentpulse.command.sys')
    def test_cwd_set_to_executable_dir_when_frozen(self, mock_sys: MagicMock, mock_popen: MagicMock):
        """Working directory is set to the executable's folder when frozen."""
        mock_sys.frozen = True
        mock_sys.executable = 'C:\\Program Files\\MyApp\\app.exe'

        run_event_command(['test'], {'USAGE_MONITOR_EVENT': 'reset'})

        kwargs = mock_popen.call_args[1]
        self.assertEqual(kwargs['cwd'], Path('C:\\Program Files\\MyApp'))

    @patch('agentpulse.command.subprocess.Popen')
    def test_multiple_commands_all_executed(self, mock_popen: MagicMock):
        """All commands in the list are launched."""
        run_event_command(['cmd1', 'cmd2', 'cmd3'], {'USAGE_MONITOR_EVENT': 'reset'})

        self.assertEqual(mock_popen.call_count, 3)
        executed = [call[0][0] for call in mock_popen.call_args_list]
        self.assertEqual(executed, ['cmd1', 'cmd2', 'cmd3'])

    @patch('agentpulse.command.traceback.print_exc')
    @patch('agentpulse.command.subprocess.Popen')
    def test_one_failure_does_not_block_others(self, mock_popen: MagicMock, mock_print_exc: MagicMock):
        """A failing command does not prevent subsequent commands from running."""
        mock_popen.side_effect = [OSError('fail'), MagicMock()]

        run_event_command(['bad', 'good'], {'USAGE_MONITOR_EVENT': 'reset'})

        self.assertEqual(mock_popen.call_count, 2)
        mock_print_exc.assert_called_once()


if __name__ == '__main__':
    unittest.main()
