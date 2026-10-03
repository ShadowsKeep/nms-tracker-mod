"""
The REPAIRS screen: while the REPAIRS tab is selected, the Starship page's right-hand panel shows
the repair summary, and the Starship grids are hidden.

How it works (static scan, work/re/notes_inventory_panel.txt, key points checked in the code):
- For the Ship page the game fills the whole right-hand panel every frame before
  DrawPageSelectBar, and draws the UI later (cGcFrontendManager::Render). So writing the panel
  from the mod's after-hook on DrawPageSelectBar shows in the same frame.
- The mod only changes what the game itself rewrites on every Starship frame, so nothing has to
  be put back when REPAIRS is left: the title (SetPageTitle), stat rows 1-4 (SetStatRow, via the
  game's LiveStats), SECTION1's hidden flag (BaseStats), the top CLASS_BOX's hidden flag and the
  grids' hidden flags (DoInventoryPageBody). Never touched: SECTION1 > NATURAL's text and
  BASE_STAT_BAR5 (layout-only, the game never rewrites them), and the class badge in
  STAT_BOX > ... > SECTION2 > CLASS_BOX (the game never un-hides it). That badge stays: it shows
  the ship's class.
- Element addresses are cached only while REPAIRS is continuously on screen. Every page open
  reloads the layout (OpenPage 0x8F7860 -> 0x8DDC80) and frees the old elements, so the cache is
  dropped on any page open, on another page, a new root, or a gap between frames; each frame it is
  also re-checked by the stored element IDs with reads that can't crash on freed memory.

The game functions are declared in nmstracker/nms_ext.py (cGcFrontendPageFunctions.SetPageTitle and
.SetStatRow, static, page / row first). The page offsets below are checked per build.
"""
import ctypes
import logging
import time

from pymhf.core.memutils import map_struct

import nmspy.data.types as nms

from . import nms_ext, tab

ROOT_FROM_PAGE = 0x14478          # *(page + this) = the page's root layer (180383: SetPageTitle 0x7CD544;
                                  # NMS.py 180383.0's cGcFrontendPage.mRootNode still says 0x14468)
PAGE_ENUM_FROM_PAGE = 0x14488     # *(i32*)(page + this) = current page enum, 1 = Starship (0x6AF2E8)
BAR_UNITS = 320.0                 # a full stat bar is 320 layout units (SetStatRow's MAINBAR width)
ROWS = 4                          # rows the game's LiveStats rewrites every Starship frame
ALWAYS_HIDE = ('EDITGRP', 'FILTERS')    # edit button; sort buttons (they would sort a display store)
GRID_LAYERS = ('SQU_INV_TECH', 'SQU_INV_REGULAR', 'SQU_TECH_BIG', 'SQU_ITEM_BIG', 'TECHHEADER', 'CARGOHEADER')
# grid header labels (the game sets them every frame after each grid, 0x6AC863 / 0x6ACAC3)
LABELS = (('TECHHEADER', 'INV_TECH_LABEL', 'Repair'), ('CARGOHEADER', 'INV_MAIN_LABEL', 'Materials'))
GRIDS_HIDDEN, GRIDS_MOD, GRIDS_REAL = 'hidden', 'mod', 'real'
MAX_GAP = 0.25                    # seconds without a frame = the inventory was closed: look up again
USER_MIN, USER_MAX = 0x10000, 0x7FFFFFFFFFFF


# ── element helpers (the game's own lookups through NMS.py's wrappers) ───────
def pointer_to(addr: int, cls):
    """A typed pointer for a game function argument declared as _Pointer[cls]."""
    return ctypes.cast(addr, ctypes.POINTER(cls))


def ok_ptr(p) -> bool:
    return isinstance(p, int) and USER_MIN <= p <= USER_MAX


def safe_read(addr: int, size: int) -> bytes:
    """Read memory that may have been freed: ctypes.string_at turns an access violation into
    OSError (a plain from_address read would crash the game instead)."""
    if not ok_ptr(addr):
        return b''
    try:
        return ctypes.string_at(addr, size)
    except OSError:
        return b''


def safe_u64(addr: int) -> int:
    raw = safe_read(addr, 8)
    return int.from_bytes(raw, 'little') if len(raw) == 8 else 0


def element_id(element: int) -> str:
    """The ID in an element's data (element +0x48 -> cGcNGuiElementData, ID TkID16 at +0x48)."""
    data = safe_u64(element + 0x48)
    return safe_read(data + 0x48, 16).split(b'\x00', 1)[0].decode('ascii', 'replace') if data else ''


def find_layer(parent: int, ident: str) -> int:
    found = map_struct(parent, nms.cGcNGuiLayer).FindLayerRecursive(ident)
    return ctypes.addressof(found) if found is not None else 0


def find_text(parent: int, ident: str) -> int:
    """A text element by ID, whether the layout made it a Text or a TextSpecial (the grid labels'
    type isn't known; both take SetText through the same vtable slot)."""
    layer = map_struct(parent, nms.cGcNGuiLayer)
    for finder in (layer.FindTextRecursive, layer.FindTextSpecialRecursive):
        found = finder(ident)
        if found is not None:
            return ctypes.addressof(found)
    return 0


def cstr(text: str) -> ctypes.Array:
    return ctypes.create_string_buffer(text.encode('utf-8', 'replace')[:255], 256)


class RepairsScreen:
    """Draw the repair summary over the Starship page while REPAIRS is selected."""

    def __init__(self, logger: logging.Logger, clock=time.monotonic):
        self.log = logger
        self.clock = clock
        self.title = 'Ship Repairs'
        self.subtitle = ''
        self.rows = []                 # core.PanelRow list
        self._cache = None             # element addresses, only while REPAIRS stays on screen
        self._last_draw = None
        self._strings = []             # this frame's C strings, alive until the next draw
        self._reported = set()

    def once(self, key, text, level=logging.INFO):
        if key not in self._reported:
            self._reported.add(key)
            self.log.log(level, text)

    def set_content(self, panel):
        self.title, self.subtitle, self.rows = panel.title, panel.subtitle, list(panel.rows)

    def forget(self):
        """Drop the cached elements (call on every page open and whenever REPAIRS isn't showing)."""
        self._cache = None
        self._last_draw = None

    def _cstr(self, text: str) -> int:
        """Address of a C string kept alive until the next draw (in case the game keeps the
        pointer until it renders the frame, rather than copying it straight away)."""
        buf = cstr(text)
        self._strings.append(buf)
        return ctypes.addressof(buf)

    def _elements(self, page: int):
        root = ctypes.c_uint64.from_address(page + ROOT_FROM_PAGE).value
        if not ok_ptr(root):
            return None
        now = self.clock()
        gap = self._last_draw is None or now - self._last_draw > MAX_GAP
        self._last_draw = now
        cached = self._cache
        if cached is not None and not gap and cached['root'] == root and all(
                element_id(addr) == ident for addr, ident in cached['check']):
            return cached
        self._cache = self._lookup(root)
        return self._cache

    def _lookup(self, root: int):
        stat_box = find_layer(root, 'STAT_BOX')
        if not stat_box:
            return None
        live = find_layer(stat_box, 'LIVE_STATS')
        found = {
            'root': root,
            'section1': find_layer(stat_box, 'SECTION1'),
            'rows': [find_layer(live, f'BASE_STAT_BAR{i}') if live else 0 for i in range(1, ROWS + 1)],
        }
        # the top CLASS_BOX (the game un-hides it every frame at 0x90613C), the edit and sort buttons
        hide = [(find_layer(root, n), n) for n in ('CLASS_BOX',) + ALWAYS_HIDE]
        grids = [(find_layer(root, n), n) for n in GRID_LAYERS]
        found['hide'] = [a for a, _ in hide if a]
        found['grids'] = [a for a, _ in grids if a]
        found['labels'] = []
        for header, ident, text in LABELS:
            parent = next((a for a, n in grids if n == header and a), 0)
            label = find_text(parent, ident) if parent else 0
            if label:
                found['labels'].append((label, ident, text.encode()))
        named = [(found['section1'], 'SECTION1'), (stat_box, 'STAT_BOX')] + hide + grids
        named += [(a, f'BASE_STAT_BAR{i + 1}') for i, a in enumerate(found['rows'])]
        named += [(a, n) for a, n, _t in found['labels']]
        found['check'] = [(a, n) for a, n in named if a]
        self.once('found', f'REPAIRS screen: {sum(1 for r in found["rows"] if r)} rows, '
                           f'{len(found["hide"])} layers to hide, {len(found["grids"])} grid layers, '
                           f'{len(found["labels"])} grid labels, section1={"yes" if found["section1"] else "no"}')
        return found

    def draw(self, page: int, grids: str = GRIDS_HIDDEN):
        """Call from the after-hook on DrawPageSelectBar while REPAIRS is showing. `grids` says what
        the left side shows this frame: the mod's grids (GRIDS_MOD: relabel their headers), the
        real ones (GRIDS_REAL, while an item is held), or nothing (GRIDS_HIDDEN)."""
        try:
            if ctypes.c_int32.from_address(page + PAGE_ENUM_FROM_PAGE).value != tab.SHIP_PAGE:
                self.forget()
                return
            el = self._elements(page)
            if el is None:
                self.once('noel', 'REPAIRS screen: panel elements not found', logging.WARNING)
                return
            self._strings = []
            nms_ext.cGcFrontendPageFunctions.SetPageTitle(
                pointer_to(page, nms.cGcFrontendPage), self._cstr(self.title), self._cstr(self.subtitle), False)
            if el['section1']:
                tab.set_hidden(el['section1'], True)
            for i, row in enumerate(el['rows']):
                if not row:
                    continue
                if i < len(self.rows):
                    r = self.rows[i]
                    width = max(0.0, min(100.0, float(r.fill))) * BAR_UNITS / 100.0
                    nms_ext.cGcFrontendPageFunctions.SetStatRow(
                        pointer_to(row, nms.cGcNGuiLayer), self._cstr(r.label), self._cstr(r.value), width, 0.0, 0, 0)
                else:
                    tab.set_hidden(row, True)
            for layer in el['hide']:
                tab.set_hidden(layer, True)
            if grids == GRIDS_HIDDEN:
                for layer in el['grids']:
                    tab.set_hidden(layer, True)       # the game shows these again itself next frame
            elif grids == GRIDS_MOD:
                for label, _ident, text in el['labels']:
                    tab.set_text(label, text)         # the game sets its own label again every frame
            self.once(f'drawn-{grids}', f'REPAIRS screen drawn (grids: {grids})')
        except Exception:
            self.forget()
            if 'error' not in self._reported:          # once: this runs every frame
                self._reported.add('error')
                self.log.exception('Drawing the REPAIRS screen failed')
