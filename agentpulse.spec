# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Agents Pulse.

Build:
  pyinstaller agentpulse.spec
"""

a = Analysis(
    ['agentpulse/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('locale/*.json', 'locale'),
        ('agentpulse/popup/popup.html', 'agentpulse/popup'),
        ('agentpulse/popup/popup.css', 'agentpulse/popup'),
        ('agentpulse/popup/popup.js', 'agentpulse/popup'),
        ('agentpulse/dashboard/index.html', 'agentpulse/dashboard'),
        ('agentpulse/dashboard/dashboard.css', 'agentpulse/dashboard'),
        ('agentpulse/dashboard/dashboard.js', 'agentpulse/dashboard'),
    ],
    hiddenimports=[
        'pystray._win32',
        'pystray._util',
        'pystray._util.win32',
        'webview',
        'webview.platforms.edgechromium',
        'clr_loader',
        'pythonnet',
        'bottle',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'unittest', 'test',
        'xmlrpc', 'pydoc',
        'tkinter', '_tkinter',
        'PIL._avif', 'PIL._webp',
        'PIL._imagingcms', 'PIL._imagingmath', 'PIL._imagingtk', 'PIL._imagingmorph',
        'setuptools', '_distutils_hack',
        'asyncio', 'concurrent',
        'multiprocessing',
        'xml', 'tomllib',
        'sqlite3',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AgentsPulse',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon='agentpulse.ico',
    version='version_info.py',
)
