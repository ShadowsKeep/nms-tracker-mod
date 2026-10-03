r"""Build the player download: dist\NMSTrackerMod.zip (fixed name, so download links never change).

The zip holds one folder, NMSTrackerMod\, laid out the way NMS.py's mod folder expects:
nmstracker_mod.py (the only .py file pyMHF loads as a mod), the nmstracker\ package, README.txt
and LICENSE. Local output (logs, reports, caches) is never included.

    python tools\package.py
"""
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'NMSTrackerMod'
DIST = ROOT / 'dist'
SKIP_DIRS = {'logs', 'reports', 'MOD_SAVES', '__pycache__'}


def files():
    yield MOD / 'nmstracker_mod.py', 'NMSTrackerMod/nmstracker_mod.py'
    yield MOD / 'README.txt', 'NMSTrackerMod/README.txt'
    yield ROOT / 'LICENSE', 'NMSTrackerMod/LICENSE'
    for p in sorted((MOD / 'nmstracker').glob('*.py')):
        yield p, f'NMSTrackerMod/nmstracker/{p.name}'


def version() -> str:
    m = re.search(r"__version__ = '([^']+)'", (MOD / 'nmstracker_mod.py').read_text(encoding='utf-8'))
    return m.group(1) if m else '?'


def main() -> int:
    DIST.mkdir(exist_ok=True)
    out = DIST / 'NMSTrackerMod.zip'
    names = []
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for src, arc in files():
            if any(part in SKIP_DIRS or part.startswith('.') for part in src.relative_to(ROOT).parts[:-1]):
                continue
            z.write(src, arc)
            names.append(arc)
    print(f'{out}  (mod v{version()}, {len(names)} files)')
    for n in names:
        print('  ' + n)
    return 0


if __name__ == '__main__':
    sys.exit(main())
