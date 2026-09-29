"""Offline tests for mod/grids.py: when the REPAIRS grids replace the game's Starship grids, and
exactly which arguments DoInventory then gets (checked with ctypes' own conversion)."""
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

    from mod import display, grids, screen, tab
    HAVE_NMSPY = True
except ImportError:                                # pragma: no cover - NMS.py not installed
    HAVE_NMSPY = False


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class RepairGridsTest(unittest.TestCase):
    def setUp(self):
        self.keep = []
        self.page = self.buf(0x19700)
        ctypes.c_int32.from_address(self.page + screen.PAGE_ENUM_FROM_PAGE).value = tab.SHIP_PAGE
        ctypes.c_int32.from_address(self.page + grids.HELD_X).value = -1
        ctypes.c_int32.from_address(self.page + grids.HELD_Y).value = -1
        self.layers = {n: self.layer(n) for n in ('SQU_INV_TECH', 'SQU_INV_REGULAR', 'INV_TECH')}
        self.g = grids.RepairGrids(logging.getLogger('test.grids'), construct=display.construct_like_game)
        self.g.parts.set_items([display.Item('SHIPSLOT_DMG4', 3, 100, 1.0, 1)])
        self.g.materials.set_items([display.Item('COPPER', 0, 75, 0.0, 0), display.Item('LAND1', 5, 50, 0.0, 0)])
        self.g.active = True
        self.out = self.buf(8)

    def buf(self, n):
        b = ctypes.create_string_buffer(n)
        self.keep.append(b)
        return ctypes.addressof(b)

    def layer(self, ident):
        e, d = self.buf(0x200), self.buf(0x68)
        ctypes.c_uint64.from_address(e + 0x48).value = d
        ctypes.memmove(d + 0x48, ident.encode(), len(ident))
        return e

    def call(self, layer='SQU_INV_TECH', columns=10):
        real_store, real_actions = 0x7000_1000, 0x7000_2000
        return self.g.before_do_inventory(self.page, real_store, self.layers[layer], True, real_actions, False,
                                          self.out, -1, 5, columns, 90, False)

    def test_parts_grid_swapped_and_locked(self):
        args = self.call('SQU_INV_TECH')
        self.assertEqual(args[0], self.page)
        self.assertEqual(args[1], self.g.parts.address)
        self.assertEqual(args[2], self.layers['SQU_INV_TECH'])
        self.assertIs(args[3], False)                                    # not accessible: slots locked
        self.assertEqual(ctypes.string_at(args[4], 16), b'\0' * 16)      # no empty/broken slot actions
        self.assertEqual(args[7], 0)                                     # mask 0: draw only
        self.assertEqual(args[5:7] + args[8:], (False, self.out, 5, 10, 90, False))
        self.assertEqual(self.g.drawn, {'parts'})
        self.assertTrue(self.g.parts.ready)

    def test_arguments_convert_like_the_game_call(self):
        fd = _get_funcdef(grids.GameGrid.DoInventory._func)
        args = self.call('SQU_INV_REGULAR')
        self.assertEqual(len(fd.arg_types), len(args))
        for t, a in zip(fd.arg_types, args):
            t.from_param(a)
        self.assertEqual(args[1], self.g.materials.address)
        self.assertEqual(self.g.drawn, {'materials'})

    def test_left_alone(self):
        self.assertIsNone(self.call('INV_TECH'))                         # some other grid
        ctypes.c_int32.from_address(self.page + screen.PAGE_ENUM_FROM_PAGE).value = 0
        self.assertIsNone(self.call())                                   # another page
        ctypes.c_int32.from_address(self.page + screen.PAGE_ENUM_FROM_PAGE).value = tab.SHIP_PAGE
        self.g.active = False
        self.assertIsNone(self.call())                                   # REPAIRS not showing
        self.assertEqual(self.g.drawn, set())

    def test_held_item_keeps_the_real_grids(self):
        ctypes.c_uint64.from_address(self.page + grids.HELD_STORE).value = 0x7000_3000
        ctypes.c_int32.from_address(self.page + grids.HELD_X).value = 2
        ctypes.c_int32.from_address(self.page + grids.HELD_Y).value = 0
        self.assertIsNone(self.call())
        self.assertTrue(self.g.held)
        self.g.frame_done()
        ctypes.c_uint64.from_address(self.page + grids.HELD_STORE).value = 0
        ctypes.c_uint8.from_address(self.page + grids.HELD_FLAG).value = 1   # the other held state
        self.assertIsNone(self.call())
        ctypes.c_uint8.from_address(self.page + grids.HELD_FLAG).value = 0
        self.assertIsNotNone(self.call())

    def test_store_not_set_up_means_never_swap(self):
        g = grids.RepairGrids(logging.getLogger('test.grids'), construct=lambda addr: None)
        g.active = True
        self.g = g
        self.assertIsNone(self.call())
        self.assertTrue(g.broken)
        self.assertIsNone(self.call())

    def test_error_turns_the_grids_off(self):
        self.g.parts.apply = None                                        # anything raising
        self.assertIsNone(self.call())
        self.assertTrue(self.g.broken)

    def test_grid_columns_used(self):
        self.g.parts.set_items([display.Item(f'P{i}', 1, 100, 1.0, 1) for i in range(9)])
        self.call(columns=4)
        store = self.g.parts.address
        self.assertEqual((ctypes.c_int16.from_address(store + 0x80).value,
                          ctypes.c_int16.from_address(store + 0x82).value), (4, 3))


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class ConstructTest(unittest.TestCase):
    def test_falls_back_when_the_game_call_does_nothing(self):
        saved = FunctionHook._call
        calls = []

        def intercept(hook, *args, **kwargs):
            fd = _get_funcdef(hook._func)
            flat = fd.flatten(*args, **kwargs)
            for t, a in zip(fd.arg_types, flat):
                t.from_param(a)
            calls.append((hook._func.__name__, flat))
            return None
        FunctionHook._call = intercept
        try:
            ds = display.DisplayStore()
            self.assertTrue(ds.build(grids.construct_with_game))
        finally:
            FunctionHook._call = saved
        self.assertEqual([(name, list(args)) for name, args in calls], [('ConstructStore', [ds.address])])
        self.assertTrue(ds.ready)


if __name__ == '__main__':
    unittest.main()
