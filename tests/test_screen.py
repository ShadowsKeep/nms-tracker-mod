"""Offline tests for mod/screen.py (the REPAIRS screen) against a fake element tree in memory.

The fake page (root pointer at +0x14468, page enum at +0x14478) holds a small tree shaped like the
live InventoryPage dump from spike S3 (two CLASS_BOX layers: a top one, and one in the stats panel).
NMS.py's FindLayerRecursive is replaced by a walk of that tree, and the game calls (StatRow,
SetPageTitle) are intercepted after ctypes has converted their exact arguments.
"""
import ctypes
import logging
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('PYTEST_VERSION', '1')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from pymhf.core.functions import _get_funcdef
    from pymhf.core.hooking import FunctionHook

    from mod import core, screen, tab
    HAVE_NMSPY = True
except ImportError:                                # pragma: no cover - NMS.py not installed
    HAVE_NMSPY = False

# (name, id, parent) in walk order; like the game, a recursive lookup returns the first match
TREE = [('CLASS_BOX', 'CLASS_BOX', 'ROOT'), ('STAT_BOX', 'STAT_BOX', 'ROOT'),
        ('SECTION1', 'SECTION1', 'STAT_BOX'), ('NATURAL', 'NATURAL', 'SECTION1'),
        ('SECTION2', 'SECTION2', 'STAT_BOX'), ('BADGE', 'CLASS_BOX', 'SECTION2'),
        ('LIVE_STATS', 'LIVE_STATS', 'STAT_BOX')] + \
       [(f'BASE_STAT_BAR{i}', f'BASE_STAT_BAR{i}', 'LIVE_STATS') for i in range(1, 6)] + \
       ([(n, n, 'ROOT') for n in screen.GRID_LAYERS + screen.ALWAYS_HIDE] if HAVE_NMSPY else []) + \
       [('INV_TECH_LABEL', 'INV_TECH_LABEL', 'TECHHEADER'), ('INV_MAIN_LABEL', 'INV_MAIN_LABEL', 'CARGOHEADER')]
TEXTS = {'INV_TECH_LABEL', 'INV_MAIN_LABEL'}        # found as text elements, not layers


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def freed_page() -> int:
    """An address that was valid and has been released, like a freed game element."""
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32)
    k32.VirtualFree.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32)
    addr = k32.VirtualAlloc(None, 0x1000, 0x3000, 0x04)
    k32.VirtualFree(addr, 0, 0x8000)
    return addr


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class RepairsScreenTest(unittest.TestCase):
    def setUp(self):
        self.keep = []
        self.texts, self.calls, self.lookups = {}, [], []
        self.vtable = self.buf(0x100)
        cb = tab.SetTextFn(lambda e, t: self.texts.__setitem__(e, t))
        self.keep.append(cb)
        ctypes.c_uint64.from_address(self.vtable + tab.SET_TEXT_SLOT).value = ctypes.cast(cb, ctypes.c_void_p).value

        self.page = self.buf(screen.PAGE_ENUM_FROM_PAGE + 8)
        self.build_tree()
        ctypes.c_int32.from_address(self.page + screen.PAGE_ENUM_FROM_PAGE).value = tab.SHIP_PAGE

        L = screen.nms.cGcNGuiLayer
        self._saved = (L.FindLayerRecursive, L.FindTextRecursive, L.FindTextSpecialRecursive, FunctionHook._call)
        test = self

        def finder(layer_self, ident):
            test.lookups.append(ident)
            found = test.find(ctypes.addressof(layer_self), ident)
            return L.from_address(found) if found and ident not in TEXTS else None

        def text_finder(layer_self, ident):
            test.lookups.append(ident)
            found = test.find(ctypes.addressof(layer_self), ident)
            return L.from_address(found) if found and ident in TEXTS else None
        L.FindLayerRecursive = finder
        L.FindTextRecursive = text_finder
        L.FindTextSpecialRecursive = lambda layer_self, ident: None

        def intercept(hook, *args, **kwargs):
            fd = _get_funcdef(hook._func)
            flat = fd.flatten(*args, **kwargs)
            for t, a in zip(fd.arg_types, flat):
                t.from_param(a)
            test.calls.append((hook._func.__name__, flat))
            return None
        FunctionHook._call = intercept

        board = core.repair_board([core.Slot(1, 'A', 'A', (('CARBON', 50),)), core.Slot(2, 'B', 'B', (('SEAL', 1),)),
                                   core.Slot(3, 'C', 'C', None)], {1, 2}, {'SEAL': 1, 'CARBON': 10})
        self.panel = core.repair_panel(board, name={'CARBON': 'Carbon'}.get, max_rows=screen.ROWS)
        self.clock = Clock()
        self.log = logging.getLogger('test.screen')
        self.s = screen.RepairsScreen(self.log, clock=self.clock)
        self.s.set_content(self.panel)

    def tearDown(self):
        L = screen.nms.cGcNGuiLayer
        L.FindLayerRecursive, L.FindTextRecursive, L.FindTextSpecialRecursive, FunctionHook._call = self._saved

    def buf(self, n):
        b = ctypes.create_string_buffer(n)
        self.keep.append(b)
        return ctypes.addressof(b)

    def build_tree(self):
        """(Re)build the page's elements, as the game does on every page open."""
        self.E, self.parent = {'ROOT': self.buf(0x200)}, {}
        for name, ident, parent in TREE:
            e, d = self.buf(0x200), self.buf(0x68)
            ctypes.c_uint64.from_address(e).value = self.vtable
            ctypes.c_uint64.from_address(e + 0x48).value = d
            ctypes.memmove(d + 0x48, ident.encode(), len(ident))
            self.E[name], self.parent[name] = e, parent
        self.ids = {name: ident for name, ident, _ in TREE}
        self.hide('BASE_STAT_BAR5', True)                 # hidden by the layout
        ctypes.c_uint64.from_address(self.page + screen.ROOT_FROM_PAGE).value = self.E['ROOT']

    def find(self, parent_addr, ident):
        names = {a: n for n, a in self.E.items()}
        start = names.get(parent_addr)

        def under(n):
            while n in self.parent:
                n = self.parent[n]
                if n == start:
                    return True
            return False
        return next((self.E[n] for n, i in self.ids.items() if i == ident and under(n)), 0)

    def hidden(self, name):
        return ctypes.c_uint8.from_address(ctypes.c_uint64.from_address(self.E[name] + 0x48).value + 0x61).value

    def hide(self, name, on):
        ctypes.c_uint8.from_address(ctypes.c_uint64.from_address(self.E[name] + 0x48).value + 0x61).value = int(on)

    def game_calls(self, name):
        return [flat for n, flat in self.calls if n == name]

    def test_draw_fills_the_panel_through_the_game_helpers(self):
        self.s.draw(self.page)
        (title,) = self.game_calls('SetPageTitle')
        self.assertEqual(title[0], self.page)
        self.assertEqual((ctypes.string_at(title[1]), ctypes.string_at(title[2]), title[3]),
                         (b'Ship Repairs', b'3 damaged parts', False))
        got = [(r[0], ctypes.string_at(r[1]).decode(), ctypes.string_at(r[2]).decode(), round(r[3], 2), r[4])
               for r in self.game_calls('StatRow')]
        self.assertEqual(got, [
            (self.E['BASE_STAT_BAR1'], 'Repair now (F7)', '1', round(33.3 * 3.2, 2), 0.0),
            (self.E['BASE_STAT_BAR2'], 'Need materials', '1', round(33.3 * 3.2, 2), 0.0),
            (self.E['BASE_STAT_BAR3'], 'Need repair screen', '1', round(33.3 * 3.2, 2), 0.0),
            (self.E['BASE_STAT_BAR4'], 'Carbon', '10 / 50', round(20.0 * 3.2, 2), 0.0),
        ])
        # hidden: things the game shows again itself every Starship frame
        for name in ('SECTION1', 'CLASS_BOX') + screen.ALWAYS_HIDE + screen.GRID_LAYERS:
            self.assertEqual(self.hidden(name), 1, name)
        # never touched: layout-only things the game would not put back
        self.assertEqual(self.hidden('BADGE'), 0)            # the ship's class badge stays
        self.assertEqual(self.hidden('BASE_STAT_BAR5'), 1)   # still hidden by the layout
        self.assertEqual(self.hidden('NATURAL'), 0)
        self.assertEqual(self.texts, {})                     # no element text written directly

    def test_mod_grids_keep_the_grids_and_relabel_them(self):
        self.s.draw(self.page, screen.GRIDS_MOD)
        for name in screen.GRID_LAYERS:
            self.assertEqual(self.hidden(name), 0, name)
        for name in screen.ALWAYS_HIDE:                      # sort buttons would sort a display store
            self.assertEqual(self.hidden(name), 1, name)
        self.assertEqual(self.texts, {self.E['INV_TECH_LABEL']: b'Repair', self.E['INV_MAIN_LABEL']: b'Materials'})

    def test_real_grids_while_holding_an_item(self):
        self.s.draw(self.page, screen.GRIDS_REAL)
        for name in screen.GRID_LAYERS:
            self.assertEqual(self.hidden(name), 0, name)
        self.assertEqual(self.texts, {})                     # the game's own labels stay

    def test_fewer_rows_hide_the_rest_but_never_row_five(self):
        self.s.set_content(core.repair_panel(core.repair_board([], set(), {}), max_rows=screen.ROWS))
        self.s.draw(self.page)
        self.assertEqual([ctypes.string_at(r[1]) for r in self.game_calls('StatRow')], [b'All parts working'])
        self.assertEqual([self.hidden(f'BASE_STAT_BAR{i}') for i in (2, 3, 4)], [1, 1, 1])
        self.assertNotIn('BASE_STAT_BAR5', self.lookups)

    def test_nothing_on_other_pages(self):
        self.s.draw(self.page)
        ctypes.c_int32.from_address(self.page + screen.PAGE_ENUM_FROM_PAGE).value = 7
        self.calls.clear()
        self.s.draw(self.page)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.s._cache)

    def test_cache_only_while_repairs_stays_on_screen(self):
        self.s.draw(self.page)
        first = len(self.lookups)
        self.clock.t += 0.016
        self.s.draw(self.page)
        self.assertEqual(len(self.lookups), first)           # next frame: cached
        self.s.forget()                                      # a page open
        self.s.draw(self.page)
        self.assertEqual(len(self.lookups), 2 * first)
        self.clock.t += screen.MAX_GAP + 0.1                 # inventory closed and opened again
        self.s.draw(self.page)
        self.assertEqual(len(self.lookups), 3 * first)

    def test_rebuilt_page_with_freed_elements(self):
        """The first in-game run read a freed element after a page switch; now it is a safe miss."""
        self.s.draw(self.page)
        old_section1 = self.E['SECTION1']
        self.build_tree()                                    # new elements (root address may even match)
        ctypes.c_uint64.from_address(self.page + screen.ROOT_FROM_PAGE).value = self.s._cache['root']
        self.E['ROOT'] = self.s._cache['root']
        ctypes.c_uint64.from_address(old_section1 + 0x48).value = freed_page() + 0x100
        self.clock.t += 0.016
        with self.assertNoLogs('test.screen', logging.ERROR):
            self.s.draw(self.page)
        self.assertEqual(self.hidden('SECTION1'), 1)         # written to the new element
        self.assertEqual(len(self.game_calls('StatRow')), 8)

    def test_safe_reads(self):
        gone = freed_page()
        self.assertEqual(screen.safe_read(gone, 8), b'')
        self.assertEqual(screen.safe_u64(gone), 0)
        self.assertEqual(screen.safe_u64(0x1234), 0)         # below user space: not read at all
        self.assertEqual(screen.element_id(self.E['STAT_BOX']), 'STAT_BOX')

    def test_missing_panel_does_not_crash(self):
        self.ids['STAT_BOX'] = 'OTHER'
        self.s.draw(self.page)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
