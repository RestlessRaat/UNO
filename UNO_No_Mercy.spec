# Build on Windows: python -m PyInstaller --noconfirm UNO_No_Mercy.spec
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'main.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'assets'), 'assets')],
    hiddenimports=['websockets.asyncio.client', 'websockets.asyncio.server',
                   'uno.devil', 'uno.god'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'numpy', 'matplotlib', 'pandas', 'PIL', 'tkinter',
              'werkzeug', 'markupsafe', 'websockets.asyncio.router',
              'websockets.sync.router', 'websockets.cli'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='UNO_No_Mercy', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='UNO_No_Mercy')
