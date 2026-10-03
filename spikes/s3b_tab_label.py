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
Spike S3b: draw a "REPAIRS" label in the inventory tab row, after MULTI-TOOL. Nothing else.

After the game draws its tab row (DrawPageSelectBar, RVA 0x6C05A0) each frame, this shows the
next unused slot (OPTION<count+1>_OFF) with our text, in the game's own "not selected" style.
It changes only that unused UI element's text and hidden flag; no save or inventory data.
Clicking it does nothing yet. F9 switches the label off / on.

Run (cmd or PowerShell, game closed), from this folder:
    pymhf run s3b_tab_label.py
Open the inventory and look at the tab row. Logs go to logs\\ next to this file.

Everything here was read from the game code of build 179666 (work/re/notes_inventory_tabs.txt)
and each signature matches exactly once in that build.
"""
import ctypes
import logging
import sys
from ctypes import c_bool, c_uint64
from pathlib import Path
from typing import Annotated

from pymhf import Mod
from pymhf.core import _internal
from pymhf.core.hooking import Structure, function_hook, on_key_pressed
from pymhf.core.memutils import map_struct
from pymhf.gui.decorators import no_gui

import nmspy.data.types as nms

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'NMSTrackerMod'))
from nmstracker import game  # noqa: E402

logger = logging.getLogger('S3b')

LABEL = b'REPAIRS'
MAX_TABS = 7                 # the game's loop covers OPTION1..OPTION7
PAGE_OFFSET = 0x84BBC0       # Data + this = the frontend page (= manager + 0x2BA0)
TAB_LIST_OFFSET = 0x8A4D80   # Data + this = inventory tab list (manager + 0x5BD60): {u32 cap, u32 count, TabEntry*}
# The only caller (0x90924E) passes page = manager+0x2BA0 and list = manager+0x5BD60 (inventory
# tabs) or manager+0x5BD70 (pause-menu tabs), so the inventory list is always page + 0x591C0.
LIST_FROM_PAGE = 0x5BD60 - 0x2BA0
DUMMY_ELEMENT = 0x6E716C0    # RVA of the element GetTextSpecial returns when an ID doesn't exist
SET_TEXT_SLOT = 0x90         # cGcNGuiTextSpecial vtable slot the game calls to set text (0x6C147D)
SetTextFn = ctypes.CFUNCTYPE(None, ctypes.c_uint64, ctypes.c_char_p)


class GameTabs(Structure):
    """Only DrawPageSelectBar is hooked (after); GetTextSpecial is only called."""

    # RVA 0x6C05A0 DrawPageSelectBar(cGcFrontendPage*, cGcNGuiLayer* barTop, TabList*, bool bShow)
    @function_hook(
        "48 89 5C 24 ? 44 88 4C 24 ? 48 89 54 24 ? 55 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 ? ? ? ? "
        "48 81 EC ? ? ? ? 66 0F 6F 0D"
    )
    def DrawPageSelectBar(
        self,
        this: Annotated[int, c_uint64],
        lpBarTop: Annotated[int, c_uint64],
        lpTabList: Annotated[int, c_uint64],
        lbShow: Annotated[bool, c_bool],
    ) -> None: ...

    # RVA 0x315FB0 GetTextSpecial(cGcNGuiLayer*, TkID<0x10>*, bool bUseDefault) -> element
    @function_hook("48 89 5C 24 ? 57 48 83 EC ? 48 8B 99 ? ? ? ? 41 0F B6 F8 41 B8 02 00 00 00")
    def GetTextSpecial(
        self,
        this: Annotated[int, c_uint64],
        lpID: Annotated[int, c_uint64],
        lbUseDefault: Annotated[bool, c_bool],
    ) -> c_uint64: ...


def data_address() -> int:
    return ctypes.c_uint64.from_address(_internal.BASE_ADDRESS + game.DATA_GLOBAL).value


def text_special(layer: int, ident: str) -> int:
    """The game's own lookup; 0 when the element doesn't exist (the game returns a dummy)."""
    tk = ctypes.create_string_buffer(ident.encode()[:15], 16)
    found = int(map_struct(layer, GameTabs).GetTextSpecial(ctypes.addressof(tk), True) or 0)
    return 0 if found in (0, _internal.BASE_ADDRESS + DUMMY_ELEMENT) else found


def set_hidden(element: int, hidden: bool):
    """What the game does: byte IsHidden at element->mpElementData (+0x48) + 0x61."""
    data = ctypes.c_uint64.from_address(element + 0x48).value
    if data:
        ctypes.c_uint8.from_address(data + 0x61).value = 1 if hidden else 0


def set_text(element: int, text: bytes):
    """Call the element's own set-text through its vtable, like the game does at 0x6C1487."""
    vtable = ctypes.c_uint64.from_address(element).value
    fn = ctypes.c_uint64.from_address(vtable + SET_TEXT_SLOT).value
    SetTextFn(fn)(element, text)


@no_gui
class S3bTabLabel(Mod):
    __author__ = 'Shadowskeep LLC'
    __description__ = 'Spike S3b: draw a REPAIRS label after the last inventory tab'
    __version__ = '0.1'

    def __init__(self):
        super().__init__()
        self.enabled = True
        self.reported = set()     # log each outcome once, not every frame

    def once(self, key, text, level=logging.INFO):
        if key not in self.reported:
            self.reported.add(key)
            logger.log(level, text)

    @on_key_pressed('f9')
    def toggle(self):
        self.enabled = not self.enabled
        logger.info(f'REPAIRS label {"on" if self.enabled else "off"}')

    @GameTabs.DrawPageSelectBar.after
    def after_draw(self, this, lpBarTop, lpTabList, lbShow):
        if not (self.enabled and lbShow and lpBarTop and lpTabList):
            return
        try:
            if lpTabList - this != LIST_FROM_PAGE:
                return                                  # the pause-menu tab list, not the inventory's
            data = data_address()
            self.once('where', f'Tab bar: page = Data+{this - data:#x} (expected {PAGE_OFFSET:#x}), '
                               f'list = Data+{lpTabList - data:#x} (expected {TAB_LIST_OFFSET:#x})')
            count = ctypes.c_uint32.from_address(lpTabList + 4).value
            if not 0 < count < MAX_TABS:
                self.once(('count', count), f'Tab count {count}: no free slot, nothing drawn')
                return
            bar = map_struct(lpBarTop, nms.cGcNGuiLayer).FindLayerRecursive('PAGESELECTBAR')
            if bar is None:
                self.once('nobar', 'PAGESELECTBAR not found under the tab bar layer', logging.WARNING)
                return
            bar_addr = ctypes.addressof(bar)
            k = count + 1
            off = text_special(bar_addr, f'OPTION{k}_OFF')
            if not off:
                self.once(('noslot', k), f'OPTION{k}_OFF does not exist in this layout', logging.WARNING)
                return
            set_text(off, LABEL)
            set_hidden(off, False)
            off_s = text_special(bar_addr, f'OPTION{k}_OFF_S')
            if off_s:
                set_hidden(off_s, True)
            self.once(('drawn', k), f'REPAIRS label drawn in slot {k} (after {count} game tabs)')
        except Exception:
            self.once('error', 'Drawing the REPAIRS label failed', logging.ERROR)
            logger.exception('after_draw failed')
