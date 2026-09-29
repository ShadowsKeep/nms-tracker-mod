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

Every game address is for build 179666 (work/re/notes_inventory_tabs.txt), checked in the code;
each signature matches exactly once in that build.
"""
import ctypes
import logging
from ctypes import c_bool, c_int32, c_uint64
from typing import Annotated

from pymhf.core import _internal
from pymhf.core.hooking import Structure, function_hook
from pymhf.core.memutils import map_struct

import nmspy.data.types as nms

from . import game

LABEL = b'REPAIRS'
MAX_TABS = 7                  # the game's loop covers OPTION1..OPTION7
SHIP_PAGE = 1                 # page enum of the Starship inventory
MANAGER_OFFSET = 0x849020     # Data + this = cGcFrontendManager
PAGE_FROM_MANAGER = 0x2BA0    # manager + this = the frontend page (DrawPageSelectBar's `this`)
LIST_FROM_MANAGER = 0x5BD60   # inventory tab list {u32 cap, u32 count, TabEntry*}; pause menu is +0x5BD70
CURRENT_PAGE = 0x17018        # manager + this: current page (changes when a switch finishes)
STATE = 0x1C3F8               # manager + this: frontend state, 6 = switching page (OpenPage 0x8F70F7)
SWITCHING = 6
TARGET_PAGE = 0x2B4C          # manager + this: page being switched to
PENDING_PAGE = 0x177B8        # manager + this: -1 when no page request is pending
TAB_ENTRY_SIZE = 0x28         # {i32 page, pad, TkID<0x20> loc key at +8}
CLICKED = 0xFE                # element state byte (+0x50) the game treats as a click (0x6C150D)
DUMMY_ELEMENT = 0x6E716C0     # RVA of the element GetTextSpecial returns for unknown IDs
SET_TEXT_SLOT = 0x90          # cGcNGuiTextSpecial vtable slot the game calls to set text
SetTextFn = ctypes.CFUNCTYPE(None, ctypes.c_uint64, ctypes.c_char_p)


class GameTabs(Structure):
    # RVA 0x6C05A0 DrawPageSelectBar(page, barTop, TabList*, bool bShow) - hooked before and after
    @function_hook(
        "48 89 5C 24 ? 44 88 4C 24 ? 48 89 54 24 ? 55 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 ? ? ? ? "
        "48 81 EC ? ? ? ? 66 0F 6F 0D"
    )
    def DrawPageSelectBar(self, this: Annotated[int, c_uint64], lpBarTop: Annotated[int, c_uint64],
                          lpTabList: Annotated[int, c_uint64], lbShow: Annotated[bool, c_bool]) -> None: ...

    # RVA 0x315FB0 GetTextSpecial(layer, TkID<0x10>*, bUseDefault) -> element (called only)
    @function_hook("48 89 5C 24 ? 57 48 83 EC ? 48 8B 99 ? ? ? ? 41 0F B6 F8 41 B8 02 00 00 00")
    def GetTextSpecial(self, this: Annotated[int, c_uint64], lpID: Annotated[int, c_uint64],
                       lbUseDefault: Annotated[bool, c_bool]) -> c_uint64: ...

    # RVA 0x8F6D40 cGcFrontendManager::OpenPage(manager, page, flag) -> bool - hooked before and after
    @function_hook("40 55 56 57 41 57 48 8B EC 48 83 EC ? 8B 81 ? ? ? ? 45 0F B6 F8 48 63 F2 48 8B F9 83 F8 04")
    def OpenPage(self, this: Annotated[int, c_uint64], liPage: Annotated[int, c_int32],
                 lbFlag: Annotated[bool, c_bool]) -> c_bool: ...

    # RVA 0x8F57E0 previous/next tab (A/D), this = &manager - hooked before and after
    @function_hook("48 89 5C 24 ? 48 89 6C 24 ? 48 89 74 24 ? 48 89 7C 24 ? 41 56 48 83 EC ? 48 8B F1 40 32 FF")
    def PrevNextPage(self, this: Annotated[int, c_uint64], lbNext: Annotated[bool, c_bool]) -> None: ...

    # RVA 0x314B10 cGcFrontendManager::RequestPage(manager, page, flag) - called only, like a tab click
    @function_hook(
        "48 89 5C 24 ? 57 48 83 EC ? 8B 81 ? ? ? ? 0F 57 C0 8B FA 48 8B D9 0F 11 44 24 ? 0F 11 44 24 ? 85 C0 74"
    )
    def RequestPage(self, this: Annotated[int, c_uint64], liPage: Annotated[int, c_int32],
                    lbFlag: Annotated[bool, c_bool]) -> None: ...


# ── small helpers (reads/writes mirror what DrawPageSelectBar itself does) ───
def manager() -> int:
    data = ctypes.c_uint64.from_address(_internal.BASE_ADDRESS + game.DATA_GLOBAL).value
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
    """The game's own lookup; 0 when the element doesn't exist (the game returns a dummy)."""
    tk = ctypes.create_string_buffer(ident.encode()[:15], 16)
    found = int(map_struct(layer, GameTabs).GetTextSpecial(ctypes.addressof(tk), True) or 0)
    return 0 if found in (0, _internal.BASE_ADDRESS + DUMMY_ELEMENT) else found


def set_hidden(element: int, hidden: bool):
    """Byte IsHidden at element->mpElementData (+0x48) + 0x61, as the game writes it."""
    data = ctypes.c_uint64.from_address(element + 0x48).value
    if data:
        ctypes.c_uint8.from_address(data + 0x61).value = 1 if hidden else 0


def set_text(element: int, text: bytes):
    """The element's own set-text through its vtable, like the game does at 0x6C1487."""
    vtable = ctypes.c_uint64.from_address(element).value
    SetTextFn(ctypes.c_uint64.from_address(vtable + SET_TEXT_SLOT).value)(element, text)


def clicked(element: int) -> bool:
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
                    map_struct(mgr, GameTabs).RequestPage(SHIP_PAGE, True)
                    self.log.info('REPAIRS clicked: asked the game for the Starship page')
        except Exception:
            self.once('error', 'Drawing the REPAIRS tab failed', logging.ERROR)
            self.log.exception('after_draw failed')
        return None
