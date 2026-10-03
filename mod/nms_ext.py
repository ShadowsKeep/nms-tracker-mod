"""
Game functions this mod needs that NMS.py (180383.0) does not declare yet, written the way NMS.py
declares its own so they can be offered upstream: member functions on their game class with
`this` typed as a pointer to that class; page helpers as static functions of
cGcFrontendPageFunctions taking the page first (like NMS.py's DoToolbar and DoInventorySlots).
Each is found by a byte pattern that matches exactly once (checked with work/re/sig.py).

Taken from NMS.py instead, now that it has them: cGcFrontendManager.Activate (the page open the
mod used to call OpenPage), cGcFrontendPageFunctions.DoToolbar (the tab row), AddTimedMessage
(11 arguments), cTkLanguageManager.GetInstance, cTkLanguageManagerBase.Translate_internal,
cGcInventoryStore's constructor, cGcNGuiLayer.FindElementRecursive and its Find*Recursive helpers.

Method names here are descriptive, not the game's own (not known): RVAs in build 180383 and what
each does are in the comments.
"""
from ctypes import c_bool, c_float, c_int32, c_uint32, c_uint64
from typing import Annotated

from pymhf.core.hooking import Structure, function_hook, static_function_hook
from pymhf.extensions.ctypes import c_char_p64

from ctypes import _Pointer  # noqa: E402,I001  after pymhf: it swaps in a subscriptable _Pointer on import

import nmspy.data.exported_types as nmse  # noqa: E402
import nmspy.data.types as nms  # noqa: E402


class cGcPlayerState(Structure):
    # RVA 0x5963A0: checks and pays the cost, clears the damage, removes a blocking
    # damaged-component item, bumps the repair stat. Returns true on success.
    # bFullCost = not FullyInstalled; discounted cost = ceil(amount * DamageRepairFactor).
    @function_hook(
        "48 8B C4 4C 89 40 ? 89 50 ? 48 89 48 ? 55 53 48 8D 68 ? 48 81 EC ? ? ? ? 48 89 70 ? 33 DB "
        "48 89 78 ? 41 0F B6 F9"
    )
    def RepairTechnology(
        self,
        this: "_Pointer[cGcPlayerState]",
        liGroup: Annotated[int, c_int32],
        lIndex: _Pointer[nmse.cGcInventoryIndex],
        lbFree: Annotated[bool, c_bool],
        lbFullCost: Annotated[bool, c_bool],
        lbUseRepairKit: Annotated[bool, c_bool],
    ) -> c_bool: ...

    # RVA 0x595FD0: GetElement + can-afford check (read-only), same cost formula.
    @function_hook(
        "48 89 5C 24 ? 57 48 83 EC ? 48 8B C2 48 8B F9 48 8B C8 49 8B D1 41 8B D8 E8 ? ? ? ? 48 85 C0 "
        "74 ? 44 0F B6 4C 24"
    )
    def CanRepairTechnology(
        self,
        this: "_Pointer[cGcPlayerState]",
        lpStore: _Pointer[nms.cGcInventoryStore],
        liGroup: Annotated[int, c_int32],
        lIndex: _Pointer[nmse.cGcInventoryIndex],
        lbFullCost: Annotated[bool, c_bool],
    ) -> c_bool: ...


class cGcPlayerInventories(Structure):
    """The object at cGcPlayerState + 0x900 that resolves inventory groups. Class name not confirmed."""

    # RVA 0x47DDA0: the store for an inventory group (0 suit, 4 ship general, 5 ship tech,
    # 6 ship cargo, 24 = steps of the open repair screen); liSubIndex -1 means "current ship /
    # vehicle". Group -1 falls through to the Exosuit store.
    @function_hook(
        "48 89 5C 24 ? 57 48 83 EC ? 48 8B D9 41 83 F8 FF 75 ? 8B CA 83 E9 04 74 ? 83 E9 01 74 ? 83 E9 01 "
        "74 ? 83 E9 04 74 ? 83 F9 01 75 ? 48 8B 03 44 8B 80 ? ? ? ? EB ? 48 8B 03 44 8B 80 ? ? ? ? 33 FF "
        "83 FA FF 0F 45 FA 83 FF FF 75 ? 48 8D 43"
    )
    def GetInventory(
        self,
        this: "_Pointer[cGcPlayerInventories]",
        liGroup: Annotated[int, c_int32],
        liSubIndex: Annotated[int, c_int32],
    ) -> c_uint64: ...


class cGcFrontendManager(Structure):
    """Functions only; fields are NMS.py's cGcFrontendManager."""

    # RVA 0x8FAD60: the previous / next page of the inventory tab row (A/D, Q/E), with wrap;
    # it ends in cGcFrontendManager.Activate (NMS.py).
    @function_hook("48 89 5C 24 ? 48 89 6C 24 ? 48 89 74 24 ? 48 89 7C 24 ? 41 56 48 83 EC ? 48 8B F1 40 32 FF")
    def PrevNextPage(
        self,
        this: "_Pointer[cGcFrontendManager]",
        lbNext: Annotated[bool, c_bool],
    ) -> None: ...

    # RVA 0x314B40: ask for a page, as a click on a tab does.
    @function_hook(
        "48 89 5C 24 ? 57 48 83 EC ? 8B 81 ? ? ? ? 0F 57 C0 8B FA 48 8B D9 0F 11 44 24 ? 0F 11 44 24 ? 85 C0 74"
    )
    def RequestPage(
        self,
        this: "_Pointer[cGcFrontendManager]",
        lePage: Annotated[int, c_int32],
        lbFlag: Annotated[bool, c_bool],
    ) -> None: ...


class cGcFrontendPageFunctions(Structure):
    """Static page helpers NMS.py's cGcFrontendPageFunctions does not have yet."""

    # RVA 0x7CD430: TITLE (or TITLE_RES) > MAINTEXT / SUBTEXT.
    @static_function_hook(
        "4C 8B DC 55 56 57 48 83 EC ? 66 0F 6F 0D ? ? ? ? 48 8B F2 49 8B E8 49 89 5B ? 41 0F B6 D9 4D 89 73"
    )
    @staticmethod
    def SetPageTitle(
        lpPage: _Pointer[nms.cGcFrontendPage],
        lpacMain: c_char_p64,
        lpacSub: c_char_p64,
        lbUseTitleRes: Annotated[bool, c_bool],
    ): ...

    # RVA 0x6B1240: one inventory grid for a store: sizes it, then DoInventorySlots (NMS.py), whose
    # lbDisabled is `not lbAccessible` and lPopupActions is liPopupActions (< 0 = the default for
    # the mode, 0 = draw only: every slot handler returns right after drawing). Returns at once if
    # the layer is hidden. Slot actions find their store by group + index, looked up from
    # lpInventory (work/re/notes_inventory_grid.txt).
    @static_function_hook(
        "44 88 4C 24 ? 4C 89 44 24 ? 55 41 55 41 56 48 81 EC ? ? ? ? 49 8B E8 4C 8B EA "
        "4C 8B F1 4D 85 C0 74 ? 49 8B 40 ? 80 78 ? 00 0F 85"
    )
    @staticmethod
    def DoInventory(
        lpPage: _Pointer[nms.cGcFrontendPage],
        lpInventory: _Pointer[nms.cGcInventoryStore],
        lpInventoryGuiLayer: _Pointer[nms.cGcNGuiLayer],   # SQU_INV_TECH, SQU_INV_REGULAR, ...
        lbAccessible: Annotated[bool, c_bool],
        lEmptySlotActions: c_uint64,                       # tk_vector<u32>* (as NMS.py types it)
        lbViewOnly: Annotated[bool, c_bool],
        lpbOut: c_uint64,                                  # bool*
        liPopupActions: Annotated[int, c_int32],
        liMinRows: Annotated[int, c_int32],
        liSlotsWide: Annotated[int, c_int32],
        liSlotSize: Annotated[int, c_int32],
        lbNoScroll: Annotated[bool, c_bool],
    ): ...

    # RVA 0x6ADEE0: fills one stat row (STAT_NAME text, BAR > PERCENT text, MAINBAR width,
    # BONUSBAR, ICON) and un-hides it. A full bar is 320.
    @static_function_hook(
        "48 89 5C 24 ? 48 89 74 24 ? 55 57 41 54 41 56 41 57 48 8B EC 48 83 EC ? 48 8B 41 ? 4C 8B FA "
        "48 8B F1 0F 29 74 24 ? 4D 8B F0 0F 57 C0 0F 11 45"
    )
    @staticmethod
    def SetStatRow(
        lpRow: _Pointer[nms.cGcNGuiLayer],                 # BASE_STAT_BARn
        lpacLabel: c_char_p64,
        lpacValue: c_char_p64,
        lfMainWidth: Annotated[float, c_float],
        lfBonusWidth: Annotated[float, c_float],
        luIconId: Annotated[int, c_uint32],
        liCompare: Annotated[int, c_int32],
    ): ...
