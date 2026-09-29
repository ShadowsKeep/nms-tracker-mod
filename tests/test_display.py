"""Offline tests for mod/display.py: display-only stores read back through NMS.py's own
cGcInventoryStore mapping and the mod's readers, like the game's memory would be."""
import ctypes
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('PYTEST_VERSION', '1')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from mod import core, display, game
    HAVE_NMSPY = True
except ImportError:                                # pragma: no cover - NMS.py not installed
    HAVE_NMSPY = False


def built(capacity=display.CAPACITY):
    ds = display.DisplayStore(capacity=capacity)
    assert ds.build(display.construct_like_game)
    return ds


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class DisplayStoreTest(unittest.TestCase):
    def items(self, n):
        return [display.Item(f'ITEM{i}', i, 50, 0.0, game.SUBSTANCE) for i in range(n)]

    def test_reads_back_like_a_game_store(self):
        ds = built()
        ds.set_items([display.Item('SHIPSLOT_DMG4', 3, 100, 1.0, game.TECHNOLOGY, True),
                      display.Item('SHIPROCKETS', -1, 100, 1.0, game.TECHNOLOGY, False),
                      display.Item('CARBON', 10, 50, 0.0, game.SUBSTANCE)])
        ds.set_style(3, 2)
        self.assertEqual(ds.apply(10), 3)
        store = game.store_at(ds.address)
        self.assertEqual((int(store.miWidth), int(store.miHeight), int(store.miCapacity)), (10, 1, 10))
        self.assertEqual(game.elements(store), [
            ('SHIPSLOT_DMG4', 0, 0, 3, 1.0, game.TECHNOLOGY, True),
            ('SHIPROCKETS', 1, 0, -1, 1.0, game.TECHNOLOGY, False),
            ('CARBON', 2, 0, 10, 0.0, game.SUBSTANCE, True),
        ])
        self.assertEqual([int(e.MaxAmount) for e in store.mStore], [100, 100, 50])
        self.assertEqual(ctypes.c_int16.from_address(ds.address + 0xB8).value, 3)
        self.assertEqual(ctypes.c_int32.from_address(ds.address + 0x100).value, 2)

    def test_valid_bits_only_for_occupied_slots(self):
        ds = built()
        ds.set_items(self.items(13))
        ds.apply(10)
        bits = [ctypes.c_uint64.from_address(ds.address + y * 8).value for y in range(16)]
        self.assertEqual(bits[:3], [0x3FF, 0x7, 0])
        self.assertEqual(set(bits[2:]), {0})

    def test_map_left_as_the_constructor_made_it(self):
        """The grid reads the hash map at +0x208 for every tech element; a blank one crashes the game."""
        ds = built()
        before = ctypes.string_at(ds.address + 0x200, 0x48)
        ds.set_items(self.items(5))
        ds.apply(10)
        self.assertEqual(ctypes.string_at(ds.address + 0x200, 0x48), before)
        self.assertTrue(ds.ready)
        self.assertEqual(ctypes.c_uint64.from_address(ds.address + 0x218).value, ds.address + 0x230)

    def test_not_ready_until_built(self):
        ds = display.DisplayStore()
        self.assertFalse(ds.ready)
        self.assertFalse(ds.build(lambda addr: None))       # a constructor that did nothing
        self.assertFalse(ds.ready)

    def test_nothing_points_into_other_data(self):
        ds = built()
        ds.set_items(self.items(3))
        ds.apply(10)
        for off in (0x98, 0xC0, 0xD0):                 # history, special slots, base stats
            self.assertEqual(ctypes.string_at(ds.address + off, 16), b'\0' * 16, hex(off))

    def test_rows_wrap_and_cap_at_sixteen(self):
        ds = built()
        ds.set_items(self.items(23))
        self.assertEqual(ds.apply(10), 23)
        store = game.store_at(ds.address)
        self.assertEqual(int(store.miHeight), 3)
        self.assertEqual(game.elements(store)[-1][1:3], (2, 2))     # 23rd item at x=2, y=2
        ds.set_items(self.items(200))
        self.assertEqual(ds.apply(10), 160)                          # 16 rows of 10
        self.assertEqual(int(store.miHeight), 16)
        self.assertEqual(ds.apply(8), 128)                           # the grid's own column count wins

    def test_rewritten_in_place_and_game_changes_undone(self):
        ds = built()
        store_addr = ds.address
        ds.set_items(self.items(5))
        ds.apply(10)
        elements_addr = ctypes.c_uint64.from_address(store_addr + 0x90).value
        ctypes.c_int32.from_address(elements_addr + 0x18).value = 999        # e.g. a sort merged stacks
        ctypes.c_uint32.from_address(store_addr + 0x9C).value = 7            # history count
        ds.apply(10)
        self.assertEqual(ds.address, store_addr)
        self.assertEqual(ctypes.c_uint64.from_address(store_addr + 0x90).value, elements_addr)
        self.assertEqual(game.elements(game.store_at(store_addr))[0][3], 0)
        self.assertEqual(ctypes.c_uint32.from_address(store_addr + 0x9C).value, 0)

    def test_empty(self):
        ds = built()
        ds.apply(10)
        store = game.store_at(ds.address)
        self.assertEqual((int(store.miHeight), game.elements(store)), (1, []))


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class ItemsTest(unittest.TestCase):
    def test_part_items_copy_the_real_elements(self):
        board = core.repair_board([core.Slot((4, 5, 1), 'SHIPSLOT_DMG4', 'x', (('CU', 5),)),
                                   core.Slot((5, 2, 1), 'SHIPROCKETS', 'x', None)], {(4, 5, 1)}, {})
        rows = {(4, 5, 1): ('SHIPSLOT_DMG4', 5, 1, 3, 1.0, 1, True),
                (5, 2, 1): ('SHIPROCKETS', 2, 1, -1, 1.0, 1, False)}
        self.assertEqual(display.part_items(board.parts, rows), [
            display.Item('SHIPSLOT_DMG4', 3, 100, 1.0, 1, True),
            display.Item('SHIPROCKETS', -1, 100, 1.0, 1, False)])

    def test_material_items_read_have_over_needed(self):
        mats = [core.BoardMaterial('CU', 75, 0), core.BoardMaterial('SEAL', 2, 9), core.BoardMaterial('ODD', 1, 0)]
        kinds = {'CU': game.SUBSTANCE, 'SEAL': game.PRODUCT}.get
        self.assertEqual(display.material_items(mats, kinds), [
            display.Item('CU', 0, 75, 0.0, game.SUBSTANCE, True),
            display.Item('SEAL', 2, 2, 0.0, game.PRODUCT, True)])      # have capped at needed; ODD unknown


if __name__ == '__main__':
    unittest.main()
