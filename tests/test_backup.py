"""Backup tests. Everything runs in a temp folder; the real save folder is never touched."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mod import backup  # noqa: E402


class BackupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.saves = tmp / 'st_1'
        self.root = tmp / 'backups'
        self.saves.mkdir()
        for name in ('save.hg', 'mf_save.hg', 'save2.hg', 'mf_save2.hg', 'save3.hg', 'accountdata.hg'):
            (self.saves / name).write_bytes(name.encode() * 10)
        os.utime(self.saves / 'save.hg', (1_700_000_000, 1_700_000_000))

    def tearDown(self):
        self._tmp.cleanup()

    def test_slot_files(self):
        self.assertEqual([f.name for f in backup.slot_files(self.saves, 1)],
                         ['save.hg', 'mf_save.hg', 'save2.hg', 'mf_save2.hg'])
        self.assertEqual([f.name for f in backup.slot_files(self.saves, 2)], ['save3.hg'])
        self.assertEqual(backup.slot_files(self.saves, 3), [])

    def test_copies_bytes_and_times_and_leaves_source_alone(self):
        src = self.saves / 'save.hg'
        before = (src.read_bytes(), src.stat().st_mtime_ns)
        dest = backup.backup_slot(self.saves, 1, root=self.root, now=datetime(2026, 9, 29, 12, 0, 0))
        self.assertEqual(dest.name, '20260929-120000-slot1')
        self.assertEqual((dest / 'save.hg').read_bytes(), before[0])
        self.assertEqual((dest / 'save.hg').stat().st_mtime_ns, before[1])
        self.assertEqual((src.read_bytes(), src.stat().st_mtime_ns), before)
        self.assertFalse((dest / 'accountdata.hg').exists())
        info = json.loads((dest / backup.MARKER).read_text(encoding='utf-8'))
        self.assertEqual(info['slot'], 1)
        self.assertEqual(len(info['files']), 4)

    def test_same_second_gets_a_new_folder(self):
        t = datetime(2026, 9, 29, 12, 0, 0)
        a = backup.backup_slot(self.saves, 1, root=self.root, now=t)
        b = backup.backup_slot(self.saves, 1, root=self.root, now=t)
        self.assertNotEqual(a, b)
        self.assertTrue(b.name.endswith('-2'))

    def test_prunes_only_its_own_folders_per_slot(self):
        foreign = self.root / '20000101-000000-slot1'     # looks like ours but has no marker
        foreign.mkdir(parents=True)
        t = datetime(2026, 9, 29, 12, 0, 0)
        other = backup.backup_slot(self.saves, 2, root=self.root, now=t)
        made = [backup.backup_slot(self.saves, 1, root=self.root, keep=5, now=t + timedelta(minutes=i))
                for i in range(7)]
        kept = [d for d in made if d.exists()]
        self.assertEqual(kept, made[-5:])
        self.assertTrue(foreign.exists())
        self.assertTrue(other.exists())

    def test_backup_all_covers_every_account_and_slot(self):
        other = self.saves.parent / 'st_2'
        other.mkdir()
        (other / 'save5.hg').write_bytes(b'x')          # slot 3 only
        (self.saves.parent / 'not_an_account').mkdir()
        made = backup.backup_all(saves=self.saves.parent, root=self.root, now=datetime(2026, 9, 29, 12, 0, 0))
        names = sorted(d.name for d in made)
        self.assertEqual(names, ['20260929-120000-slot1', '20260929-120000-slot2', '20260929-120000-slot3'])

    def test_missing_slot_raises(self):
        with self.assertRaises(FileNotFoundError):
            backup.backup_slot(self.saves, 4, root=self.root)
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
