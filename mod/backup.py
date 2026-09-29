"""
Save backups taken before the mod repairs anything.

Read-only on the game's files: each save file is opened 'rb', read once and closed, and the
copy is written under %LOCALAPPDATA%\\ShadowskeepLLC\\NMSTrackerMod\\backups\\. Pruning only ever
touches folders this module created (they carry MARKER), and keeps the newest KEEP per slot.
"""
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

MARKER = '.nmstracker-backup'
KEEP = 5
RETRIES = 3


def default_root() -> Path:
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(base) / 'ShadowskeepLLC' / 'NMSTrackerMod' / 'backups'


def save_root() -> Path:
    base = os.environ.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
    return Path(base) / 'HelloGames' / 'NMS'


def slot_files(save_dir, slot: int) -> list:
    """The files that make up one save slot: auto + manual save, each with its manifest.

    Slot N uses save index 2N-1 (auto) and 2N (manual); index 1 is plain 'save.hg'.
    """
    save_dir = Path(save_dir)
    names = []
    for index in (2 * slot - 1, 2 * slot):
        stem = 'save' if index == 1 else f'save{index}'
        names += [f'{stem}.hg', f'mf_{stem}.hg']
    return [save_dir / n for n in names if (save_dir / n).is_file()]


def _copy_stable(src: Path, dest: Path) -> None:
    """Copy one file, retrying if the game rewrites it while we read."""
    for attempt in range(RETRIES):
        before = src.stat()
        with open(src, 'rb') as fh:
            data = fh.read()
        after = src.stat()
        if before.st_mtime_ns == after.st_mtime_ns and before.st_size == after.st_size == len(data):
            dest.write_bytes(data)
            os.utime(dest, ns=(after.st_atime_ns, after.st_mtime_ns))
            return
        time.sleep(0.5 * (attempt + 1))
    raise OSError(f'{src.name} kept changing while it was being copied; try again after the game finishes saving')


def _ours(root: Path, slot: int) -> list:
    out = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        marker = d / MARKER
        if not (d.is_dir() and marker.is_file()):
            continue
        try:
            info = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if info.get('slot') == slot:
            out.append(d)
    return sorted(out, key=lambda d: d.name)


def prune(root, slot: int, keep: int = KEEP) -> list:
    """Delete this module's oldest backups of `slot`, keeping the newest `keep`."""
    ours = _ours(Path(root), slot)
    old = ours[:-keep] if keep > 0 else ours
    for d in old:
        shutil.rmtree(d)
    return old


def backup_all(saves=None, root=None, keep: int = KEEP, now=None, max_slot: int = 30) -> list:
    """Back up every save slot of every account folder (st_*). Returns the folders made."""
    made = []
    for account in sorted(Path(saves or save_root()).glob('st_*')):
        for slot in range(1, max_slot + 1):
            if slot_files(account, slot):
                made.append(backup_slot(account, slot, root=root, keep=keep, now=now))
    return made


def backup_slot(save_dir, slot: int, root=None, keep: int = KEEP, now=None) -> Path:
    """Copy every file of `slot` into a new timestamped folder and prune older ones."""
    files = slot_files(save_dir, slot)
    if not files:
        raise FileNotFoundError(f'No save files for slot {slot} in {save_dir}')
    root = Path(root) if root else default_root()
    stamp = (now or datetime.now()).strftime('%Y%m%d-%H%M%S')
    dest = root / f'{stamp}-slot{slot}'
    n = 2
    while dest.exists():
        dest = root / f'{stamp}-slot{slot}-{n}'
        n += 1
    dest.mkdir(parents=True)
    for f in files:
        _copy_stable(f, dest / f.name)
    (dest / MARKER).write_text(json.dumps({
        'slot': slot, 'source': str(Path(save_dir)), 'files': [f.name for f in files], 'made': stamp,
    }), encoding='utf-8')
    prune(root, slot, keep)
    return dest
