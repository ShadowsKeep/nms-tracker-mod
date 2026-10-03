"""
The REPAIRS screen's two grids: the game's own Starship grids, drawn from display-only copies.

While REPAIRS is on, the mod's before-hook on DoInventory swaps in its own stores for the Starship
page's two grids: damaged parts in the tech grid, the materials they need in the cargo grid. The
game draws them exactly like its own inventories, fully locked. Static scan and reasoning:
work/re/notes_inventory_grid.txt (build 179666; re-checked for 180383 in notes_180383.txt).

cGcFrontendPageFunctions.DoInventory (mod/nms_ext.py, static: page, store, layer, bAccessible,
slotActions*, bViewOnly, bool* out, mask, minRows, columns, slotSize, bNoScroll). The mod calls
through with:
- store = a DisplayStore (built with the game's constructor);
- bAccessible = False: filled slots locked (no pick-up / drag / quick transfer), no drop into
  empty slots;
- slotActions = an empty vector: the empty-slot and broken-slot popups return at once;
- mask = 0: every slot handler returns right after drawing - no hover popups, prompts, repairs,
  discards or confirms (any non-zero mask would let a Confirm on a damaged tech start a repair).
Actions in the game find their target by inventory group + slot, looked up from the store
pointer; a mod store has no group (-1), which the game maps to the EXOSUIT. So the three paths
the locks don't cover are guarded here:
- a held item dropped on a filled slot (would go to the Exosuit): while an item is held, the real
  grids are drawn instead;
- the sort buttons (sort the passed store in place): hidden by the screen while REPAIRS is on,
  and the stores are rewritten before every draw anyway;
- the game adds every drawn item to its "seen items" list (owner accepted this, 2026-09-30).
"""
import ctypes
import logging

from pymhf.core.memutils import map_struct

import nmspy.data.types as nms

from . import display, screen, tab

PARTS_LAYERS = ('SQU_INV_TECH', 'SQU_TECH_BIG')          # the Starship tech grid
MATERIAL_LAYERS = ('SQU_INV_REGULAR', 'SQU_ITEM_BIG')    # the Starship cargo grid
# build 180383 (DoInventorySlots 0x6B43D7..0x6B43F3)
HELD_STORE = 0x19650          # page + this: store of the item being held (0 = none)
HELD_X, HELD_Y = 0x19640, 0x19644   # page + this: its slot (-1 = none)
HELD_FLAG = 0x1966C           # page + this: byte, another held-item state


def construct_with_game(addr: int):
    """The game's own constructor (NMS.py's cGcInventoryStore.cGcInventoryStore) on a mod-owned
    store; falls back to the static-scan copy when the call does not leave the map's
    self-pointers in place."""
    map_struct(addr, nms.cGcInventoryStore).cGcInventoryStore()
    if ctypes.c_uint64.from_address(addr + 0x218).value != addr + 0x230:
        display.construct_like_game(addr)


def store_pointer(addr: int):
    """The display store as the _Pointer[cGcInventoryStore] DoInventory is declared with."""
    return ctypes.cast(addr, ctypes.POINTER(nms.cGcInventoryStore))


def item_held(page: int) -> bool:
    """The page is holding an item (picked up, dragged) - its drop could land on a grid slot."""
    held = (ctypes.c_uint64.from_address(page + HELD_STORE).value != 0
            and ctypes.c_int32.from_address(page + HELD_X).value != -1
            and ctypes.c_int32.from_address(page + HELD_Y).value != -1)
    return held or ctypes.c_uint8.from_address(page + HELD_FLAG).value != 0


class RepairGrids:
    """Decides, for every DoInventory call, whether to draw a display store instead."""

    def __init__(self, logger: logging.Logger, construct=construct_with_game):
        self.log = logger
        self.construct = construct
        self.parts = display.DisplayStore()
        self.materials = display.DisplayStore()
        self._no_actions = ctypes.create_string_buffer(16)       # tk_vector<u32> with count 0
        self.active = False            # the mod sets this while REPAIRS is showing
        self.broken = False            # stores could not be built: never swap
        self.drawn = set()             # which grids were swapped since the last frame_done()
        self.held = False              # an item was held this frame: real grids drawn
        self._reported = set()

    def once(self, key, text, level=logging.INFO):
        if key not in self._reported:
            self._reported.add(key)
            self.log.log(level, text)

    def build(self) -> bool:
        if self.broken:
            return False
        try:
            ok = self.parts.build(self.construct) and self.materials.build(self.construct)
        except Exception:
            self.log.exception('Building the REPAIRS grids failed')
            ok = False
        if not ok:
            self.broken = True
            self.once('broken', 'REPAIRS grids off: the display stores are not set up like the game\'s',
                      logging.ERROR)
        return ok

    def frame_done(self):
        """Call once per frame after the screen was drawn."""
        self.drawn = set()
        self.held = False

    def before_do_inventory(self, page, store, layer, accessible, actions, view_only, out_flag,
                            mask, min_rows, columns, slot_size, no_scroll):
        """Replacement arguments for DoInventory, or None to let the game draw its own grid."""
        if not self.active or self.broken:
            return None
        try:
            if ctypes.c_int32.from_address(page + screen.PAGE_ENUM_FROM_PAGE).value != tab.SHIP_PAGE:
                return None
            name = screen.element_id(layer)
            which = 'parts' if name in PARTS_LAYERS else 'materials' if name in MATERIAL_LAYERS else None
            if which is None:
                return None
            if item_held(page):
                self.held = True
                self.once('held', 'REPAIRS: an item is held, showing the real Starship grids until it is put down')
                return None
            if not self.parts.ready and not self.build():
                return None
            target = self.parts if which == 'parts' else self.materials
            shown = target.apply(columns)
            self.drawn.add(which)
            self.once(f'swap-{which}', f'REPAIRS {which} grid: {shown} slots ({name}, {columns} columns)')
            return (page, target.address, layer, False, ctypes.addressof(self._no_actions), view_only,
                    out_flag, 0, min_rows, columns, slot_size, no_scroll)
        except Exception:
            self.broken = True
            self.log.exception('REPAIRS grids failed; drawing the real grids from now on')
            return None
