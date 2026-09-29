# /// script
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_exe = true
# start_paused = true
# default_mod_save_dir = "{CURR_DIR}"
#
# [tool.pymhf.logging]
# log_dir = "{CURR_DIR}"
# log_level = "info"
# ///
"""
Spike S2: watch one repair done by hand, then repair ONE more slot the same way (go/no-go).

Part A (watch only): hooks log every call the game makes to its own repair routines, and
which inventory each call really touched. The hooks change nothing.

Part B (one repair): the "Repair one more" button does nothing until Part A has seen the
game perform a normal paid repair in this session. It then asks the game for the SAME
inventory (same group number, same lookup the repair code uses), picks the next damaged
technology there, runs the game's own can-afford check, backs up every save slot, and calls
the game's own repair with the SAME flags. It never edits the inventory itself and never
uses the free / repair-kit paths. Afterwards it checks the ship's real inventory too.

Run from cmd or PowerShell (game closed), from this folder:
    pymhf run s2_repair_one.py
Steps are in README.md.

Function locations come from a read-only static scan of NMS.exe build 179666 (work/re).
Each signature was checked to match exactly once in that build.
"""
import ctypes
import json
import logging
import sys
import time
from ctypes import c_bool, c_int32, c_uint64
from pathlib import Path
from typing import Annotated

from pymhf import Mod
from pymhf.core.hooking import Structure, function_hook
from pymhf.core.memutils import get_addressof, map_struct
from pymhf.gui.decorators import gui_button

from ctypes import _Pointer  # noqa: E402,I001  after pymhf: it swaps in a subscriptable _Pointer on import

import nmspy.data.exported_types as nmse
import nmspy.data.types as nms
from nmspy.common import gameData
from nmspy.data.enums import InventoryChoice
from nmspy.decorators import main_loop

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mod import backup  # noqa: E402

logger = logging.getLogger('S2')

STORE_SIZE = 0x248
INVENTORIES_OFFSET = 0x900          # cGcPlayerState + 0x900 is what GetInventory() is called on
# Primary-ship store offsets inside cGcPlayerState, by the group number GetInventory() uses
# (RVA 0x47BDB0, build 179666). NMS.py's InventoryChoice has 5 and 6 the other way round.
SHIP_STORES = {4: ('general', 0x7458), 5: ('tech', 0xAB28), 6: ('cargo', 0x8FC8)}
TECHNOLOGY = 1  # cGcInventoryType.Technology


class GameRepair(Structure):
    """Game functions NMS.py does not map yet. `this` is the cGcPlayerState address for these two.

    `this` is typed as a pointer to this class (a string annotation, resolved later) so the functions
    can be called on map_struct(player_state_address, GameRepair). Typing it as cGcPlayerState made
    ctypes reject the call ("expected LP_cGcPlayerState instance instead of pointer to GameRepair").
    """

    # RVA 0x5930A0: checks and pays the cost, clears the damage, removes a blocking
    # damaged-component item, bumps the repair stat. Returns true on success.
    @function_hook(
        "48 8B C4 4C 89 40 ? 89 50 ? 48 89 48 ? 55 53 48 8D 68 ? 48 81 EC ? ? ? ? 48 89 70 ? 33 DB "
        "48 89 78 ? 41 0F B6 F9"
    )
    def RepairTechnology(
        self,
        this: "_Pointer[GameRepair]",
        liGroup: Annotated[int, c_int32],
        lIndex: _Pointer[nmse.cGcInventoryIndex],
        lbFree: Annotated[bool, c_bool],
        lbFullCost: Annotated[bool, c_bool],
        lbUseRepairKit: Annotated[bool, c_bool],
    ) -> c_bool: ...

    # RVA 0x592CD0: GetElement + can-afford check (read-only), same cost formula.
    @function_hook(
        "48 89 5C 24 ? 57 48 83 EC ? 48 8B C2 48 8B F9 48 8B C8 49 8B D1 41 8B D8 E8 ? ? ? ? 48 85 C0 "
        "74 ? 44 0F B6 4C 24"
    )
    def CanRepairTechnology(
        self,
        this: "_Pointer[GameRepair]",
        lpStore: _Pointer[nms.cGcInventoryStore],
        liGroup: Annotated[int, c_int32],
        lIndex: _Pointer[nmse.cGcInventoryIndex],
        lbFullCost: Annotated[bool, c_bool],
    ) -> c_bool: ...

    # RVA 0x4E4630: cGcInventoryStore, clears a "Broken" special slot (crashed-ship slots paid in
    # units by the REPAIR_SLOT popup, which pays separately). Watched only; never called here.
    @function_hook("85 D2 0F 88 ? ? ? ? 48 89 54 24 ? 57 48 83 EC ? 0F BF 81 ? ? ? ? 48 8B F9 3B D0 0F 8D")
    def RepairBrokenSlot(
        self,
        this: _Pointer[nms.cGcInventoryStore],
        lIndex: Annotated[int, c_uint64],   # cGcInventoryIndex passed by value (X low, Y high)
    ) -> c_bool: ...


class GameInventories(Structure):
    """Lives at cGcPlayerState + 0x900. Only called, never hooked (it has hundreds of callers)."""

    # RVA 0x47BDB0: the store for an inventory group; subIndex -1 means "current ship/vehicle".
    # Group 24 (0x18) resolves through a handle to whatever object the menu is showing.
    @function_hook(
        "48 89 5C 24 ? 57 48 83 EC ? 48 8B D9 41 83 F8 FF 75 ? 8B CA 83 E9 04 74 ? 83 E9 01 74 ? 83 E9 01 "
        "74 ? 83 E9 04 74 ? 83 F9 01 75 ? 48 8B 03 44 8B 80 ? ? ? ? EB ? 48 8B 03 44 8B 80 ? ? ? ? 33 FF "
        "83 FA FF 0F 45 FA 83 FF FF 75 ? 48 8D 43"
    )
    def GetInventory(
        self,
        this: Annotated[int, c_uint64],
        liGroup: Annotated[int, c_int32],
        liSubIndex: Annotated[int, c_int32],
    ) -> c_uint64: ...


def _xy(packed):
    x = packed & 0xFFFFFFFF
    y = (packed >> 32) & 0xFFFFFFFF
    return (x - (1 << 32) if x & 0x80000000 else x, y - (1 << 32) if y & 0x80000000 else y)


def _plausible(store):
    try:
        w, h, cap, n = int(store.miWidth), int(store.miHeight), int(store.miCapacity), len(store.mStore)
    except Exception:
        return False
    return 0 <= w <= 16 and 0 <= h <= 16 and 0 <= cap <= 256 and 0 <= n <= max(cap, w * h, 1)


def _store(ps_addr, offset, index):
    return map_struct(ps_addr + offset + index * STORE_SIZE, nms.cGcInventoryStore)


def _row(el):
    """(id, x, y, amount, damage, type) for one element. Type is a c_enum32: use .value, not int()."""
    return (str(el.Id).strip('\x00 '), int(el.Index.X), int(el.Index.Y), int(el.Amount),
            float(el.DamageFactor), int(getattr(el.Type, 'value', el.Type)))


def _elements(store):
    if not _plausible(store):
        return []
    return [row for row in (_row(el) for el in store.mStore) if row[0]]


def _at(store, x, y):
    return [e for e in _elements(store) if (e[1], e[2]) == (x, y)]


def _specials(store):
    if not _plausible(store) or len(store.maSpecialSlots) > 256:
        return []
    return [(int(s.Index.X), int(s.Index.Y), getattr(s.Type, 'name', str(s.Type))) for s in store.maSpecialSlots]


def _known_stores(ps):
    """Address -> label for every store the player state owns, to tell a real store from a copy."""
    ps_addr = get_addressof(ps)
    out = {}
    for i in range(0x21):
        name = InventoryChoice(i).name if i in InventoryChoice._value2member_map_ else hex(i)
        out[ps_addr + 0x910 + i * STORE_SIZE] = f'inventories[{name}]'
    for ship in range(12):
        for _group, (label, offset) in SHIP_STORES.items():
            out[ps_addr + offset + ship * STORE_SIZE] = f'ship[{ship}].{label}'
    return out


def _label(ps, addr):
    if not addr:
        return 'none'
    return _known_stores(ps).get(addr, f'NOT a player store (0x{addr:X})')


def _materials(ps):
    """Every non-technology item the player holds in personal and primary-ship stores: {id: count}."""
    ps_addr = get_addressof(ps)
    primary = int(ps.miPrimaryShip)
    stores = [ps.mInventories[i] for i in range(0x21)]
    stores += [_store(ps_addr, off, primary) for _, off in SHIP_STORES.values()]
    totals = {}
    for store in stores:
        for gid, _x, _y, amount, _dmg, typ in _elements(store):
            if typ != TECHNOLOGY and amount > 0:
                totals[gid] = totals.get(gid, 0) + amount
    return totals


def _diff(before, after):
    keys = set(before) | set(after)
    return {k: after.get(k, 0) - before.get(k, 0) for k in sorted(keys) if after.get(k, 0) != before.get(k, 0)}


def _real_ship_damage(ps):
    """Damaged technology in the primary ship's own stores: [(store, id, x, y, damage)].

    The menu repairs through a separate per-part store (group 24) whose coordinates are not the
    ship's, so the real ship is checked as a whole rather than at the menu's (x, y).
    """
    ps_addr = get_addressof(ps)
    primary = int(ps.miPrimaryShip)
    out = []
    for label, off in SHIP_STORES.values():
        for gid, x, y, _amount, dmg, typ in _elements(_store(ps_addr, off, primary)):
            if typ == TECHNOLOGY and dmg > 0:
                out.append((label, gid, x, y, round(dmg, 3)))
    return out


def _contents(store):
    return [(gid, x, y, round(dmg, 3)) for gid, x, y, _amount, dmg, _typ in _elements(store)]


def _inventories(ps):
    return map_struct(get_addressof(ps) + INVENTORIES_OFFSET, GameInventories)


class S2RepairOne(Mod):
    __author__ = 'Shadowskeep LLC'
    __description__ = 'Spike S2: watch a manual repair, then repair one more slot the same way'
    __version__ = '0.4'

    def __init__(self):
        super().__init__()
        self.observed = None      # group + flags from the last normal paid repair the game did
        self._pending = False
        self._busy = False        # True while our own call runs, so our hooks do not "observe" it
        self._last_can = None

    # ── Part A: watch ────────────────────────────────────────────────────────
    @GameRepair.RepairTechnology.after
    def after_repair(self, this, liGroup, lIndex, lbFree, lbFullCost, lbUseRepairKit, _result_):
        # Record first, diagnose after: a failure in the extra logging must not hide the repair.
        try:
            ps = gameData.player_state
            is_ps = ps is not None and get_addressof(this) == get_addressof(ps)
            x, y = int(lIndex.contents.X), int(lIndex.contents.Y)
            logger.info(f'[watch] RepairTechnology(group={liGroup}, slot={x},{y}, free={bool(lbFree)}, '
                        f'fullCost={bool(lbFullCost)}, repairKit={bool(lbUseRepairKit)}) -> {bool(_result_)} '
                        f'this_is_player_state={is_ps}{" (our call)" if self._busy else ""}')
            if is_ps and not self._busy and _result_ and not lbFree and not lbUseRepairKit:
                self.observed = {'group': int(liGroup), 'free': False, 'full_cost': bool(lbFullCost),
                                 'repair_kit': False, 'slot': [x, y]}
                logger.info('[watch] Normal paid repair seen. "Repair one more" is now enabled. '
                            'Leave the other damaged part for the button.')
        except Exception:
            logger.exception('after_repair recording failed')
            return
        try:
            if is_ps:
                store_addr = int(_inventories(ps).GetInventory(int(liGroup), -1) or 0)
                label = _label(ps, store_addr)
                if self.observed is not None and self.observed['group'] == int(liGroup):
                    self.observed['store'] = label
                menu = _contents(map_struct(store_addr, nms.cGcInventoryStore)) if store_addr else []
                logger.info(f'[watch]   group {liGroup} is {label}, now holding {menu}')
                logger.info(f'[watch]   ship damage now: {_real_ship_damage(ps)}')
        except Exception:
            logger.exception('after_repair diagnostics failed (the repair was still recorded)')

    @GameRepair.CanRepairTechnology.after
    def after_can_repair(self, this, lpStore, liGroup, lIndex, lbFullCost, _result_):
        try:
            x, y = int(lIndex.contents.X), int(lIndex.contents.Y)
            key = (int(liGroup), x, y, bool(lbFullCost), bool(_result_))
            if key == self._last_can:       # the menu may ask every frame; log changes only
                return
            self._last_can = key
            ps = gameData.player_state
            is_ps = ps is not None and get_addressof(this) == get_addressof(ps)
            store_addr = get_addressof(lpStore) if lpStore else 0
            item = _at(map_struct(store_addr, nms.cGcInventoryStore), x, y) if store_addr else []
            logger.info(f'[watch] CanRepairTechnology(group={key[0]}, slot={x},{y}, fullCost={key[3]}) '
                        f'-> {key[4]} this_is_player_state={is_ps} store={_label(ps, store_addr) if ps else "?"} '
                        f'item={item}')
        except Exception:
            logger.exception('after_can_repair logging failed')

    @GameRepair.RepairBrokenSlot.after
    def after_broken_slot(self, this, lIndex, _result_):
        try:
            x, y = _xy(int(lIndex))
            ps = gameData.player_state
            label = _label(ps, get_addressof(this)) if ps else '?'
            logger.info(f'[watch] RepairBrokenSlot(store={label}, slot={x},{y}) -> {bool(_result_)}'
                        ' (units repair; watched only)')
        except Exception:
            logger.exception('after_broken_slot logging failed')

    # ── Part B: one repair, mirroring the game ───────────────────────────────
    @gui_button('Repair next step (keep the part\'s repair screen open)')
    def request_repair(self):
        if self.observed is None:
            logger.warning('Not yet: first repair one damaged slot yourself in the normal inventory menu.')
            return
        self._pending = True
        logger.info('Repair requested; it runs on the next game frame.')

    @main_loop.after
    def on_frame(self):
        if not self._pending:
            return
        self._pending = False
        try:
            self._repair_one()
        except Exception:
            logger.exception('S2 repair failed')
        finally:
            self._busy = False

    def _repair_one(self):
        ps = gameData.player_state
        if ps is None:
            logger.warning('No player state; load a save.')
            return
        obs = self.observed
        group = obs['group']
        ps_addr = get_addressof(ps)
        store_addr = int(_inventories(ps).GetInventory(group, -1) or 0)
        label = _label(ps, store_addr)
        if not store_addr:
            logger.error(f'The game has no inventory for group {group} right now. '
                         'Keep the inventory open on the ship, like when you repaired by hand.')
            return
        if group == 24 and label.startswith('inventories['):
            logger.error('No repair screen is open. In the game, select the damaged part so its repair '
                         'steps are showing, then press the button again.')
            return
        store = map_struct(store_addr, nms.cGcInventoryStore)
        if not _plausible(store):
            logger.error(f'Inventory for group {group} ({label}) does not look valid; stopping.')
            return

        targets = [e for e in _elements(store) if e[5] == TECHNOLOGY and e[4] > 0]
        if not targets:
            logger.info(f'No damaged step left in group {group} ({label}): {_contents(store)}')
            return

        # Ask the game about each damaged part and take the first one it says you can afford.
        caller = map_struct(ps_addr, GameRepair)
        idx = nmse.cGcInventoryIndex()
        chosen = None
        for gid, x, y, *_ in targets:
            idx.X, idx.Y = x, y
            can = caller.CanRepairTechnology(ctypes.byref(store), group, ctypes.byref(idx), obs['full_cost'])
            if can is None:     # pyMHF returns None when the call itself failed; that is not a "no"
                logger.error(f'Could not ask the game about {gid} (call failed, see error above); stopping.')
                return
            logger.info(f'  {gid} at {x},{y}: game says can repair = {bool(can)}')
            if can:
                chosen = (gid, x, y)
                break
        if chosen is None:
            logger.info(f'None of the {len(targets)} damaged parts in group {group} ({label}) can be '
                        'afforded according to the game; nothing done.')
            return
        gid, x, y = chosen
        idx.X, idx.Y = x, y
        logger.info(f'Target {gid} at {x},{y} in group {group} ({label}).')

        made = []
        for account in sorted(backup.save_root().glob('st_*')):
            for slot in range(1, 16):
                if backup.slot_files(account, slot):
                    made.append(str(backup.backup_slot(account, slot)))
        if not made:
            logger.error('Could not back up any save; stopping without repairing.')
            return
        logger.info(f'Backed up {len(made)} save slot(s).')

        before_mats = _materials(ps)
        before = {'menu_store': _contents(store), 'ship_damage': _real_ship_damage(ps), 'special': _specials(store)}

        self._busy = True
        ok = caller.RepairTechnology(group, ctypes.byref(idx), False, obs['full_cost'], False)
        self._busy = False
        if ok is None:
            logger.error('The repair call itself failed (see error above). Check the game; the backup is: '
                         f'{made[0]}')

        after = {'menu_store': _contents(store), 'ship_damage': _real_ship_damage(ps), 'special': _specials(store)}
        report = {
            'made': time.strftime('%Y-%m-%d %H:%M:%S'),
            'target': {'id': gid, 'x': x, 'y': y, 'group': group, 'store': label},
            'mirrored_flags': obs,
            'result': None if ok is None else bool(ok),
            'materials_change': _diff(before_mats, _materials(ps)),
            'before': before,
            'after': after,
            'backups': made,
        }
        path = HERE / f"s2-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        logger.info(f'RepairTechnology returned {report["result"]}. Materials change: {report["materials_change"]}. '
                    f'Repair steps now: {after["menu_store"]}. Ship damage now: {after["ship_damage"]}. '
                    f'Report: {path}')
