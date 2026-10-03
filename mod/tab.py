"""
The REPAIRS tab in the inventory tab row (from spike S3c v0.2, verified in game 2026-09-29).

REPAIRS is "the Starship page + a flag this module keeps": the game never sees a page enum it
doesn't know. The mod's hooks call into RepairsTab:
- after the game draws its tab row (DrawPageSelectBar), draw REPAIRS in the next free slot, in
  the selected look while active (Starship then gets the not-selected look), and pick up clicks
  with the game's own click test;
- around A/D (PrevNextPage) and every page open (OpenPage), land on / leave REPAIRS by
  adjusting the page the game's own code opens;
- OpenPage only starts a page switch (state 6 + target page); the current page changes frames
  later, so "switching to Starship" counts as still on REPAIRS.

The game functions are declared in mod/nms_ext.py (cGcFrontendManager.OpenPage / PrevNextPage /
RequestPage, cGcFrontendPage.DrawPageSelectBar); the mod's hooks pass plain addresses in here.
The manager fields below are build 180383's (work/re/notes_180383.txt); the frontend manager's
place in Data comes from NMS.py.
"""
import ctypes
import logging

from pymhf.core.memutils import map_struct

import nmspy.data.types as nms

from . import game, nms_ext

LABEL = b'REPAIRS'
MAX_TABS = 7                  # the game's loop covers OPTION1..OPTION7
SHIP_PAGE = 1                 # page enum of the Starship inventory
# Build 180383 (work/re/notes_180383.txt). Data + MANAGER_OFFSET = cGcFrontendManager, from NMS.py.
MANAGER_OFFSET = nms.cGcApplication.Data.mFrontendManager.offset   # 0x859090
PAGE_FROM_MANAGER = 0x2BA0    # manager + this = the page DoToolbar gets (RenderPage 0x905CDE; NMS.py's mPage says 0x2790)
LIST_FROM_MANAGER = 0x5BD80   # inventory tab list {u32 cap, u32 count, TabEntry*} (Activate 0x8FC3EF); pause menu +0x5BD90
CURRENT_PAGE = 0x17028        # manager + this: current page (changes when a switch finishes; Activate 0x8FC669)
STATE = 0x1C408               # manager + this: frontend state, 6 = switching page (Activate 0x8FC677)
SWITCHING = 6
TARGET_PAGE = 0x2B4C          # manager + this: page being switched to (Activate 0x8FC681)
PENDING_PAGE = 0x177C8        # manager + this: -1 when no page request is pending (DoToolbar 0x6C53EE)
TAB_ENTRY_SIZE = 0x28         # {i32 page, pad, TkID<0x20> loc key at +8}
CLICKED = 0xFE                # element state byte (+0x50) the game treats as a click (DoToolbar 0x6C53DD)
SET_TEXT_SLOT = 0x90          # cGcNGuiTextSpecial vtable slot the game calls to set text
SetTextFn = ctypes.CFUNCTYPE(None, ctypes.c_uint64, ctypes.c_char_p)


# ── small helpers (reads/writes mirror what DrawPageSelectBar itself does) ───
def manager() -> int:
    data = game.data_address()
    return data + MANAGER_OFFSET if data else 0


def i32(addr: int) -> int:
    return ctypes.c_int32.from_address(addr).value


def heading_to_ship(mgr: int) -> bool:
    """On the Starship page, or the game is switching to it."""
    return i32(mgr + CURRENT_PAGE) == SHIP_PAGE or (
        i32(mgr + STATE) == SWITCHING and i32(mgr + TARGET_PAGE) == SHIP_PAGE)


def tab_pages(tab_list: int) -> list:
    count = ctypes.c_uint32.from_address(tab_list + 4).value
    entries = ctypes.c_uint64.from_address(tab_list + 8).value
    if not entries or not 0 < count <= MAX_TABS:
        return []
    return [i32(entries + i * TAB_ENTRY_SIZE) for i in range(count)]


def tab_key(tab_list: int, index: int) -> str:
    entries = ctypes.c_uint64.from_address(tab_list + 8).value
    return ctypes.string_at(entries + index * TAB_ENTRY_SIZE + 8, 0x20).split(b'\x00', 1)[0].decode('ascii', 'replace')


def text_special(layer: int, ident: str) -> int:
    """A Text_Special element (OPTION1, OPTION1_OFF, ...) through NMS.py's FindTextSpecialRecursive
    (the game's own FindElementRecursive); 0 when it doesn't exist."""
    found = map_struct(layer, nms.cGcNGuiLayer).FindTextSpecialRecursive(ident)
    return ctypes.addressof(found) if found is not None else 0


def set_hidden(element: int, hidden: bool):
    """cGcNGuiElement.mpElementData->IsHidden (NMS.py), the flag the game itself writes."""
    data = map_struct(element, nms.cGcNGuiElement).mpElementData
    if data:
        data.contents.IsHidden = bool(hidden)


def set_text(element: int, text: bytes):
    """The element's own set-text through its vtable, like the game does at 0x6C1487."""
    vtable = ctypes.c_uint64.from_address(element).value
    SetTextFn(ctypes.c_uint64.from_address(vtable + SET_TEXT_SLOT).value)(element, text)


def clicked(element: int) -> bool:
    """Input state byte at element + 0x50 (not in NMS.py's cGcNGuiElement), 0xFE = clicked."""
    return ctypes.c_uint8.from_address(element + 0x50).value == CLICKED


def find_bar(bar_top: int):
    bar = map_struct(bar_top, nms.cGcNGuiLayer).FindLayerRecursive('PAGESELECTBAR')
    return ctypes.addressof(bar) if bar is not None else 0


class RepairsTab:
    """State and hook bodies for the REPAIRS tab. Hook bodies never raise and return None
    unless they replace a game function's arguments (pyMHF disables a hook that raises)."""

    def __init__(self, logger: logging.Logger, on_change=None):
        self.log = logger
        self.on_change = on_change or (lambda active, why: None)
        self.enabled = True
        self.active = False          # REPAIRS is the selected tab
        self.showing = False         # REPAIRS was drawn selected this frame (the panel may be drawn)
        self.entering = False        # we asked the game for the Starship page to show REPAIRS
        self.intent = None           # (bNext, was_active, current page) while the game's A/D code runs
        self._next_active = None     # decided in OpenPage-before, applied in OpenPage-after if it opened
        self._reported = set()

    def once(self, key, text, level=logging.INFO):
        if key not in self._reported:
            self._reported.add(key)
            self.log.log(level, text)

    def select(self, on: bool, why: str):
        if on != self.active:
            self.log.info(f'REPAIRS {"selected" if on else "left"} ({why})')
            self.active = on
            try:
                self.on_change(on, why)
            except Exception:
                self.log.exception('on_change failed')
        self.active = on
        if not on:
            self.showing = False

    def set_enabled(self, on: bool):
        self.enabled = on
        self.active = self.entering = self.showing = False

    def inventory_bar(self, page: int, tab_list: int) -> bool:
        mgr = manager()
        return bool(mgr) and page == mgr + PAGE_FROM_MANAGER and tab_list == mgr + LIST_FROM_MANAGER

    # ── A/D ─────────────────────────────────────────────────────────────────
    def before_prev_next(self, this, lbNext):
        try:
            mgr = manager()
            if self.enabled and mgr:
                self.intent = (bool(lbNext), self.active, i32(mgr + CURRENT_PAGE))
        except Exception:
            self.log.exception('before_prev_next failed')

    def after_prev_next(self, this, lbNext):
        self.intent = None

    # ── every page open goes through here ───────────────────────────────────
    def before_open(self, this, liPage, lbFlag):
        try:
            self._next_active = None
            if not self.enabled or this != manager():
                return None
            page = int(liPage)
            if self.intent is not None:
                going_next, was_active, current = self.intent
                pages = tab_pages(this + LIST_FROM_MANAGER)
                if pages and current in pages:
                    if was_active:
                        self._next_active = False
                        return (this, pages[0] if going_next else pages[-1], lbFlag)
                    at_end = current == (pages[-1] if going_next else pages[0])
                    if at_end and len(pages) < MAX_TABS:
                        self._next_active = True             # wrap onto REPAIRS instead
                        self.entering = False
                        return (this, SHIP_PAGE, lbFlag)
                self._next_active = False
                return None
            if self.entering:
                self._next_active = page == SHIP_PAGE
                self.entering = False
                return None
            self._next_active = False                           # any other page open leaves REPAIRS
            return None
        except Exception:
            self.log.exception('before_open failed')
            return None

    def after_open(self, this, liPage, lbFlag, result):
        try:
            if self._next_active is not None and result:
                self.select(self._next_active, f'page {int(liPage)} opened')
            self._next_active = None
        except Exception:
            self.log.exception('after_open failed')

    # ── clicks on Starship while REPAIRS is shown: handle before the game sees them ─
    def before_draw(self, this, lpBarTop, lpTabList, lbShow):
        try:
            if not (self.enabled and self.active and lbShow and self.inventory_bar(this, lpTabList)):
                return None
            mgr = this - PAGE_FROM_MANAGER
            if not heading_to_ship(mgr):
                self.select(False, 'page is no longer Starship')
                return None
            if i32(mgr + CURRENT_PAGE) != SHIP_PAGE:
                return None                                     # still switching
            bar = find_bar(lpBarTop)
            pages = tab_pages(lpTabList)
            if not bar or SHIP_PAGE not in pages:
                return None
            ship_off = text_special(bar, f'OPTION{pages.index(SHIP_PAGE) + 1}_OFF')
            if ship_off and clicked(ship_off):
                ctypes.c_uint8.from_address(ship_off + 0x50).value = 0   # consume: the page is already open
                self.select(False, 'Starship clicked')
        except Exception:
            self.log.exception('before_draw failed')
        return None

    # ── draw REPAIRS (and fix Starship's look while REPAIRS is selected) ──────
    def after_draw(self, this, lpBarTop, lpTabList, lbShow):
        self.showing = False
        if not (self.enabled and lbShow and lpBarTop and lpTabList):
            return None
        try:
            if not self.inventory_bar(this, lpTabList):
                return None
            mgr = this - PAGE_FROM_MANAGER
            pages = tab_pages(lpTabList)
            if not pages or len(pages) >= MAX_TABS:
                return None
            bar = find_bar(lpBarTop)
            if not bar:
                return None
            k = len(pages) + 1
            on, off, off_s = (text_special(bar, f'OPTION{k}{s}') for s in ('', '_OFF', '_OFF_S'))
            if not (on and off):
                self.once(('noslot', k), f'OPTION{k} / OPTION{k}_OFF missing', logging.WARNING)
                return None
            if off_s:
                set_hidden(off_s, True)
            current = i32(mgr + CURRENT_PAGE)
            if self.active and current == SHIP_PAGE:
                set_text(on, LABEL)
                set_hidden(on, False)
                set_hidden(off, True)
                if SHIP_PAGE in pages:                          # show Starship as not selected
                    j = pages.index(SHIP_PAGE) + 1
                    ship_on, ship_off = text_special(bar, f'OPTION{j}'), text_special(bar, f'OPTION{j}_OFF')
                    if ship_on and ship_off:
                        name = game.translate_key(tab_key(lpTabList, j - 1)) or 'STARSHIP'
                        set_text(ship_off, name.encode('utf-8', 'replace'))
                        set_hidden(ship_on, True)
                        set_hidden(ship_off, False)
                self.showing = True
                return None
            if self.active and not heading_to_ship(mgr):
                self.select(False, 'page is no longer Starship')
            set_text(off, LABEL)                                # not selected (or still switching)
            set_hidden(off, False)
            set_hidden(on, True)
            self.once(('drawn', k), f'REPAIRS tab drawn in slot {k} (after {len(pages)} game tabs)')
            if clicked(off) and not self.entering and not self.active:
                if current == SHIP_PAGE:
                    self.select(True, 'clicked on the Starship page')
                elif i32(mgr + PENDING_PAGE) == -1:
                    self.entering = True
                    map_struct(mgr, nms_ext.cGcFrontendManager).RequestPage(SHIP_PAGE, True)
                    self.log.info('REPAIRS clicked: asked the game for the Starship page')
        except Exception:
            self.once('error', 'Drawing the REPAIRS tab failed', logging.ERROR)
            self.log.exception('after_draw failed')
        return None
