"""Build and archive the Windows portable application; no source downloads."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-only", action="store_true")
    args = parser.parse_args()
    if not (ROOT / 'assets/manifest.json').exists():
        raise SystemExit('Prepare assets first (see README).')
    if not args.archive_only:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', str(ROOT / 'UNO_No_Mercy.spec')], cwd=ROOT, check=True)
    dest = ROOT / 'dist/UNO_No_Mercy'
    for name in ('README.md', 'CREDITS.md'):
        shutil.copy2(ROOT / name, dest / name)
    shutil.copytree(ROOT / 'docs', dest / 'docs', dirs_exist_ok=True)
    shutil.copytree(ROOT / 'examples', dest / 'examples', dirs_exist_ok=True)
    archive = ROOT / 'dist/UNO_No_Mercy_Windows.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for file in sorted(dest.rglob('*')):
            if file.is_file():
                z.write(file, file.relative_to(dest.parent))
    print(json.dumps({'executable': str(dest / 'UNO_No_Mercy.exe'), 'archive': str(archive), 'zip_bytes': archive.stat().st_size}))


if __name__ == '__main__':
    main()
