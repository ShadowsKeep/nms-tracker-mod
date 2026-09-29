# /// script
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_exe = true
# start_paused = true
# default_mod_save_dir = "{CURR_DIR}"
#
# [tool.pymhf.gui]
# shown = false
#
# [tool.pymhf.logging]
# shown = false
# log_dir = "{CURR_DIR}"
# log_level = "info"
# ///
"""
Spike S3c: make the REPAIRS tab selectable (click, and A/D), styled like the game's own tabs.

The REPAIRS tab is "the Starship page + a mod flag": the game never sees a page it doesn't know.
- Click REPAIRS: select it; if another page is open, ask the game for the Starship page the same
  way its own tab click does (RequestPage), and select REPAIRS when that page opens.
- While selected: REPAIRS gets the selected (yellow) look, Starship the not-selected look.
- A/D: D from the last tab and A from the first land on REPAIRS; from REPAIRS, D goes to the
  first tab and A to the last. Done by adjusting the page the game's own A/D code opens.
- Any other page opening (another tab, closing and reopening) clears the flag.
The content under REPAIRS is still the Starship grid; showing repairs there is the next step.
F9 switches the whole thing off / on.

Run (cmd or PowerShell, game closed), from this folder:
    pymhf run s3c_tab_select.py

All addresses are from build 179666 (work/re/notes_inventory_tabs.txt), checked in the code;
each signature matches exactly once in that build.
"""
import ctypes
import logging
import sys
from ctypes import c_bool, c_int32, c_uint64
from pathlib import Path
from typing import Annotated

from pymhf import Mod
from pymhf.core import _internal
from pymhf.core.hooking import Structure, function_hook, on_key_pressed
from pymhf.core.memutils import map_struct
from pymhf.gui.decorators import no_gui

import nmspy.data.types as nms

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mod import game  # noqa: E402

logger = logging.getLogger('S3c')

LABEL = b'REPAIRS'
MAX_TABS = 7                  # the game's loop covers OPTION1..OPTION7
SHIP_PAGE = 1                 # page enum of the Starship inventory
MANAGER_OFFSET = 0x849020     # Data + this = cGcFrontendManager (page = manager + 0x2BA0)
PAGE_FROM_MANAGER = 0x2BA0
LIST_FROM_MANAGER = 0x5BD60   # inventory tab list {u32 cap, u32 count, TabEntry*}; pause menu is +0x5BD70
CURRENT_PAGE = 0x17018        # manager + this = current page enum (updated when a page switch finishes)
# OpenPage (0x8F70F7) starts a page switch by setting state = 6 and the target page; the current
# page only changes a few frames later (0x8F7860). Seen in game: checking the current page right
# after OpenPage dropped REPAIRS 4 ms after A/D selected it.
STATE = 0x1C3F8               # manager + this: frontend state, 6 = switching page
SWITCHING = 6
TARGET_PAGE = 0x2B4C          # manager + this: the page being switched to
PENDING_PAGE = 0x177B8        # manager + this = -1 when no page change is pending
TAB_ENTRY_SIZE = 0x28         # {i32 page, pad, TkID<0x20> loc key at +8}
CLICKED = 0xFE                # element state byte (+0x50) the game treats as a click (0x6C150D)
DUMMY_ELEMENT = 0x6E716C0     # RVA of the element GetTextSpecial returns for unknown IDs
SET_TEXT_SLOT = 0x90
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


# ── small helpers (all reads/writes mirror what DrawPageSelectBar itself does) ──────────
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
    tk = ctypes.create_string_buffer(ident.encode()[:15], 16)
    found = int(map_struct(layer, GameTabs).GetTextSpecial(ctypes.addressof(tk), True) or 0)
    return 0 if found in (0, _internal.BASE_ADDRESS + DUMMY_ELEMENT) else found


def set_hidden(element: int, hidden: bool):
    data = ctypes.c_uint64.from_address(element + 0x48).value
    if data:
        ctypes.c_uint8.from_address(data + 0x61).value = 1 if hidden else 0


def set_text(element: int, text: bytes):
    vtable = ctypes.c_uint64.from_address(element).value
    SetTextFn(ctypes.c_uint64.from_address(vtable + SET_TEXT_SLOT).value)(element, text)


def clicked(element: int) -> bool:
    return ctypes.c_uint8.from_address(element + 0x50).value == CLICKED


@no_gui
class S3cTabSelect(Mod):
    __author__ = 'Shadowskeep LLC'
    __description__ = 'Spike S3c: selectable REPAIRS inventory tab'
    __version__ = '0.2'

    def __init__(self):
        super().__init__()
        self.enabled = True
        self.active = False         # REPAIRS is the selected tab
        self.entering = False       # we asked the game for the Starship page to show REPAIRS
        self.intent = None          # (bNext, was_active, current page) while the game's A/D code runs
        self._next_active = None    # decided in OpenPage-before, applied in OpenPage-after if it opened
        self.reported = set()

    def once(self, key, text, level=logging.INFO):
        if key not in self.reported:
            self.reported.add(key)
            logger.log(level, text)

    def select(self, on: bool, why: str):
        if on != self.active:
            logger.info(f'REPAIRS {"selected" if on else "left"} ({why})')
            if on:
                try:
                    game.show_message('NMS Tracker: REPAIRS tab selected (repair list comes next)')
                except Exception:
                    logger.exception('message failed')
        self.active = on

    @on_key_pressed('f9')
    def toggle(self):
        self.enabled = not self.enabled
        self.active = self.entering = False
        logger.info(f'REPAIRS tab {"on" if self.enabled else "off"}')

    def inventory_bar(self, this, lpTabList) -> bool:
        mgr = manager()
        return bool(mgr) and this == mgr + PAGE_FROM_MANAGER and lpTabList == mgr + LIST_FROM_MANAGER

    # ── A/D ─────────────────────────────────────────────────────────────────
    @GameTabs.PrevNextPage.before
    def before_prev_next(self, this, lbNext):
        try:
            mgr = manager()
            if self.enabled and mgr:
                self.intent = (bool(lbNext), self.active, i32(mgr + CURRENT_PAGE))
        except Exception:
            logger.exception('before_prev_next failed')

    @GameTabs.PrevNextPage.after
    def after_prev_next(self, this, lbNext):
        self.intent = None

    # ── every page open goes through here ───────────────────────────────────
    @GameTabs.OpenPage.before
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
                        target = pages[0] if going_next else pages[-1]
                        self._next_active = False
                        return (this, target, lbFlag)
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
            logger.exception('before_open failed')
            return None

    @GameTabs.OpenPage.after
    def after_open(self, this, liPage, lbFlag, _result_):
        try:
            if self._next_active is not None and _result_:
                self.select(self._next_active, f'page {int(liPage)} opened')
            self._next_active = None
        except Exception:
            logger.exception('after_open failed')

    # ── clicks on Starship while REPAIRS is shown: handle before the game sees them ─
    @GameTabs.DrawPageSelectBar.before
    def before_draw(self, this, lpBarTop, lpTabList, lbShow):
        try:
            if not (self.enabled and self.active and lbShow and self.inventory_bar(this, lpTabList)):
                return None
            mgr = this - PAGE_FROM_MANAGER
            if not heading_to_ship(mgr):
                self.select(False, 'page is no longer Starship')
                return None
            if i32(mgr + CURRENT_PAGE) != SHIP_PAGE:
                return None                                     # still switching: nothing to consume yet
            bar = map_struct(lpBarTop, nms.cGcNGuiLayer).FindLayerRecursive('PAGESELECTBAR')
            pages = tab_pages(lpTabList)
            if bar is None or SHIP_PAGE not in pages:
                return None
            ship_off = text_special(ctypes.addressof(bar), f'OPTION{pages.index(SHIP_PAGE) + 1}_OFF')
            if ship_off and clicked(ship_off):
                ctypes.c_uint8.from_address(ship_off + 0x50).value = 0   # consume: the page is already open
                self.select(False, 'Starship clicked')
        except Exception:
            logger.exception('before_draw failed')
        return None

    # ── draw REPAIRS (and fix Starship's look while REPAIRS is selected) ──────
    @GameTabs.DrawPageSelectBar.after
    def after_draw(self, this, lpBarTop, lpTabList, lbShow):
        if not (self.enabled and lbShow and lpBarTop and lpTabList):
            return None
        try:
            if not self.inventory_bar(this, lpTabList):
                return None
            mgr = this - PAGE_FROM_MANAGER
            pages = tab_pages(lpTabList)
            if not pages or len(pages) >= MAX_TABS:
                return None
            bar = map_struct(lpBarTop, nms.cGcNGuiLayer).FindLayerRecursive('PAGESELECTBAR')
            if bar is None:
                return None
            bar_addr = ctypes.addressof(bar)
            k = len(pages) + 1
            on, off, off_s = (text_special(bar_addr, f'OPTION{k}{s}') for s in ('', '_OFF', '_OFF_S'))
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
                    ship_on = text_special(bar_addr, f'OPTION{j}')
                    ship_off = text_special(bar_addr, f'OPTION{j}_OFF')
                    if ship_on and ship_off:
                        name = game.translate_key(tab_key(lpTabList, j - 1)) or 'STARSHIP'
                        set_text(ship_off, name.encode('utf-8', 'replace'))
                        set_hidden(ship_on, True)
                        set_hidden(ship_off, False)
                self.once('drawn-on', f'REPAIRS drawn selected in slot {k}')
                return None
            if self.active and not heading_to_ship(mgr):
                self.select(False, 'page is no longer Starship')
            # not selected yet (or still switching to Starship): the not-selected look
            set_text(off, LABEL)
            set_hidden(off, False)
            set_hidden(on, True)
            self.once(('drawn', k), f'REPAIRS drawn in slot {k} (after {len(pages)} game tabs)')
            if clicked(off) and not self.entering and not self.active:
                if current == SHIP_PAGE:
                    self.select(True, 'clicked on the Starship page')
                elif i32(mgr + PENDING_PAGE) == -1:
                    self.entering = True
                    map_struct(mgr, GameTabs).RequestPage(SHIP_PAGE, True)
                    logger.info('REPAIRS clicked: asked the game for the Starship page')
        except Exception:
            self.once('error', 'Drawing the REPAIRS tab failed', logging.ERROR)
            logger.exception('after_draw failed')
        return None
