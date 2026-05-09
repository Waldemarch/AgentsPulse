"""Stub Windows-only modules so the test suite runs on Linux CI."""
import sys
import types

def _stub(name: str) -> None:
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)

_stub('winreg')
_stub('ctypes.windll')
