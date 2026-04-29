"""Run Agents Pulse from `python -m agentpulse` or the bundled EXE."""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import traceback

VERBOSE = '--verbose' in sys.argv

if VERBOSE and getattr(sys, 'frozen', False):
    from agentpulse.verbose import setup_console

    setup_console()

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_ssize_t(-4))

if VERBOSE:
    from agentpulse.verbose import print_startup_diagnostics

    print_startup_diagnostics()

import webview  # type: ignore[import-untyped]

from agentpulse.app import AgentPulse, crash_log
from agentpulse.single_instance import ensure_single_instance, release_instance_lock

if VERBOSE:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-5s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

_state: dict[str, AgentPulse] = {}


def _log_step(label: str) -> None:
    if VERBOSE:
        print(f'  [startup] {label}', flush=True)


def _destroy_webviews() -> None:
    for window in list(webview.windows):
        try:
            window.destroy()
        except Exception:
            pass


def _run_tray() -> None:
    try:
        if VERBOSE:
            from agentpulse.verbose import print_runtime_diagnostics

            print_runtime_diagnostics()
        _log_step('creating AgentPulse')
        app = AgentPulse()
        _state['app'] = app
        _log_step('running tray icon')
        app.run()
    except Exception:
        error = traceback.format_exc()
        _log_step(f'crash: {error}')
        crash_log(error)
    finally:
        _destroy_webviews()


def _restart_if_requested() -> None:
    app = _state.get('app')
    if not app or not app.restart_requested:
        return
    release_instance_lock()
    if getattr(sys, 'frozen', False):
        env = {key: value for key, value in os.environ.items() if not key.startswith(('_PYI_', '_MEI'))}
        subprocess.Popen([sys.executable], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.Popen([sys.executable, '-m', 'agentpulse'], creationflags=subprocess.CREATE_NO_WINDOW)


try:
    _log_step('single-instance check')
    if not ensure_single_instance():
        _log_step('another instance is active')
        sys.exit(0)

    _log_step('starting webview loop')
    webview.create_window('', html='', hidden=True)
    webview.start(func=_run_tray)
    _restart_if_requested()
except Exception:
    crash_log(traceback.format_exc())
