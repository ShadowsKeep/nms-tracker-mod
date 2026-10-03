"""
Reading the game's inventories and repair state, costs, item names, messages, and the repair
loop. Imported by the NMS.py mod (nmstracker_mod.py); tests import it offline with
PYTEST_VERSION=1 set (pyMHF prompts on import outside a real console otherwise).

Game functions come from NMS.py where it declares them, otherwise from nmstracker/nms_ext.py (declared
the NMS.py way). Structures are read with NMS.py's classes; a raw offset is used only where build
being played differs from what NMS.py describes, or where NMS.py has no field, with a note.

Proven in spike S2 (2026-09-29, build 179666): calling RepairTechnology on a repair step pays
the normal materials, repairs the step, and the game saves the result.

Rules kept here:
- Repairs only ever go through the game's own RepairTechnology, never free / repair-kit paths,
  and only after the game's own CanRepairTechnology says yes.
- Every game call runs on the game thread (callers use NMS.py's main_loop).
- A pointer is followed only after the store it belongs to looks sane, so a wrong offset in a
  future build is skipped instead of crashing the game.
"""
import ctypes
import functools
import math
import re
from ctypes import wintypes

from pymhf.core import _internal
from pymhf.core.functions import _get_funcdef
from pymhf.core.memutils import get_addressof, map_struct

import nmspy.data.exported_types as nmse
import nmspy.data.types as nms
from nmspy.common import gameData
from nmspy.data.enums import InventoryChoice

from . import core, nms_ext

GAME_BUILD = 180383
STORE_SIZE = ctypes.sizeof(nms.cGcInventoryStore)       # 0x248
_PS = nms.cGcPlayerState
INVENTORIES_ARRAY = _PS.mInventories.offset             # 0x910
# Primary-ship stores by the group number GetInventory() uses (5 = tech, 6 = cargo),
# at NMS.py's own field offsets (0x7458 / 0xAB28 / 0x8FC8 - the tech array's annotation says
# 0xAA28, but the field is laid out after the 12 cargo stores, at 0xAB28, which the game uses).
SHIP_STORES = {4: ('general', _PS.mShipInventories.offset), 5: ('tech', _PS.mShipInventoriesTechOnly.offset),
               6: ('cargo', _PS.mShipInventoriesCargo.offset)}
GROUP_REPAIR_STEPS = 24         # the steps of the part whose repair screen is open
SUBSTANCE, TECHNOLOGY, PRODUCT = 0, 1, 2   # cGcInventoryType
MAX_STEPS = 64                  # safety cap for one repair run
STEP_ID = re.compile(r'^R(\d+)_')   # repair step ids: "R%d_%.12s" (0xEA92F0)
REPAIR_ENTRY_SIZE = ctypes.sizeof(nmse.cGcRepairTechData)   # 0x1B0

# Build 180383 data locations NMS.py 180383.0 does not give (work/re/notes_180383.txt). Game
# functions are all found by byte pattern; these can move with game updates, which is why the mod
# switches itself off on other builds.
INVENTORIES_OFFSET = 0x900      # cGcPlayerState + this: the object GetInventory() is called on
REPAIR_BUFFER = 0x182A8         # cGcPlayerState + this: tk_vector<cGcRepairTechData> RepairTechBuffer
NOTIFICATIONS_OFFSET = 0x847BB0 # cGcApplication::Data + this: cGcPlayerNotifications (caller 0x3CD267)
# The on-screen message is set up the way the game's own callers do it (e.g. RVA 0x3CD216).
MESSAGE_TIME = 1.0              # display time the callers pass
MESSAGE_AUDIO = 0x3C2700B8      # the sound id the callers pass


class Names:
    """Item id -> the name the game shows, in the player's language.

    Keys come from the game's own product / substance / technology tables; text comes from the
    game's own translation. Anything that can't be resolved falls back to the id."""

    def __init__(self):
        self._keys = None
        self._kinds = {}                # id -> cGcInventoryType of the table it came from
        self._cache = {}

    def load(self, reality) -> int:
        """Read the name keys once from cGcRealityManager. Returns how many were found."""
        keys, kinds = {}, {}
        tables = [(reality.mpSubstanceTable, SUBSTANCE), (reality.mpProductTable, PRODUCT),
                  (reality.mpTechnologyTable, TECHNOLOGY)]
        for ptr, kind in tables:
            try:
                for entry in ptr.contents.Table:
                    gid = str(entry.ID).strip('\x00 ')
                    if gid:
                        kinds.setdefault(gid, kind)
                    # NameLower is the normal-case name ("Chromatic Metal"); Name is capitals.
                    key = str(getattr(entry, 'NameLower', '') or '').strip('\x00 ') or str(entry.Name).strip('\x00 ')
                    if gid and key:
                        keys.setdefault(gid, key)
            except Exception:
                continue
        self._keys, self._kinds = keys, kinds
        return len(keys)

    def kind(self, gid: str, default: int = None):
        """The item's inventory type (substance / product / technology), or `default`."""
        return self._kinds.get(gid.lstrip('^'), default)

    def name(self, gid: str, translate=None) -> str:
        gid = gid.lstrip('^')
        if gid in self._cache:
            return self._cache[gid]
        key = (self._keys or {}).get(gid)
        text = ''
        if key:
            try:
                text = (translate or translate_key)(key)
            except Exception:
                text = ''
        text = text.strip() or gid
        self._cache[gid] = text
        return text


def translate_key(key: str) -> str:
    """Translate one text key with the game's own language manager ('' if it can't)."""
    base = _internal.BASE_ADDRESS
    if base in (None, -1) or not build_supported():
        return ''
    manager = int(nms.cTkLanguageManager.GetInstance() or 0)
    if not manager:
        return ''
    raw = ctypes.create_string_buffer(key.encode('ascii', 'ignore')[:31], 32)
    lang = map_struct(manager, nms.cTkLanguageManagerBase)
    result = lang.Translate_internal(ctypes.addressof(raw), None)       # no fallback, like the game's callers
    ptr = int(getattr(result, 'value', result) or 0)                    # c_char_p64 (or a plain int)
    if not ptr:
        return ''
    return ctypes.string_at(ptr).decode('utf-8', 'replace')      # up to the NUL, like the game reads it


def data_address() -> int:
    """cGcApplication::Data, through NMS.py (gameData.GcApplication is set once the game's state
    machine starts); 0 before that."""
    app = gameData.GcApplication
    if app is None:
        return 0
    return get_addressof(app.mpData) or 0


def running_build(path: str = None) -> str:
    """FileVersion string of the running NMS.exe (e.g. '180383'), or '' if it can't be read."""
    return _file_version(path or getattr(_internal, 'BINARY_PATH', '') or '')


@functools.lru_cache(maxsize=8)
def _file_version(path: str) -> str:
    if not path:
        return ''
    ver = ctypes.WinDLL('version')
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ''
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return ''
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, '\\VarFileInfo\\Translation', ctypes.byref(ptr), ctypes.byref(length)):
        return ''
    lang, codepage = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint16 * 2)).contents
    key = f'\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileVersion'
    if not ver.VerQueryValueW(buf, key, ctypes.byref(ptr), ctypes.byref(length)) or not length.value:
        return ''
    return ctypes.wstring_at(ptr.value, length.value).rstrip('\x00').strip()


def build_supported() -> bool:
    return running_build() == str(GAME_BUILD)


def show_message(text: str) -> bool:
    """Show a timed on-screen message the way the game's own notifications appear.

    Must run on the game thread. Returns False if the game build or pointers look wrong."""
    base = _internal.BASE_ADDRESS
    if base in (None, -1) or not build_supported():
        return False
    data = data_address()
    if not data:
        return False
    ui = getattr(_globals, 'GcUIGlobals', None)
    if ui is None:
        return False
    this = data + NOTIFICATIONS_OFFSET
    hook = nms.cGcPlayerNotifications.AddTimedMessage
    message = ctypes.create_string_buffer(text.encode('utf-8')[:511], 512)   # cTkFixedString<512>
    icon = ctypes.c_int32(0)                                                 # no icon
    fn = map_struct(this, nms.cGcPlayerNotifications)
    fn.AddTimedMessage(as_arg(hook, 1, ctypes.addressof(message)), MESSAGE_TIME,
                       as_arg(hook, 3, ctypes.addressof(ui.HUDWarningColour)), MESSAGE_AUDIO,
                       as_arg(hook, 5, ctypes.addressof(icon)), False, 0.0, False, False, False)
    return True


def as_arg(hook, index: int, address: int):
    """`address` as the exact pointer type NMS.py declared for argument `index` (0 = this) of a
    game function. Needed because e.g. cTkFixedString[512] makes a new class each time it is
    written, and ctypes only accepts the declared one."""
    return ctypes.cast(address, _get_funcdef(hook._func).arg_types[index])


# ── readers ──────────────────────────────────────────────────────────────────
def plausible(store) -> bool:
    """Sanity check before following any pointer in a store."""
    try:
        w, h, cap, n = int(store.miWidth), int(store.miHeight), int(store.miCapacity), len(store.mStore)
    except Exception:
        return False
    return 0 <= w <= 16 and 0 <= h <= 16 and 0 <= cap <= 256 and 0 <= n <= max(cap, w * h, 1)


def row(el):
    """(id, x, y, amount, damage, type, installed) for one element. Enum fields need .value."""
    return (str(el.Id).strip('\x00 '), int(el.Index.X), int(el.Index.Y), int(el.Amount),
            float(el.DamageFactor), int(getattr(el.Type, 'value', el.Type)), bool(el.FullyInstalled))


def elements(store) -> list:
    if not plausible(store):
        return []
    return [r for r in (row(el) for el in store.mStore) if r[0]]


def contents(store) -> list:
    return [(gid, x, y, round(dmg, 3)) for gid, x, y, _amount, dmg, *_ in elements(store)]


def damaged_tech(store) -> list:
    return [r for r in elements(store) if r[5] == TECHNOLOGY and r[4] > 0]


def store_at(address: int):
    return map_struct(address, nms.cGcInventoryStore)


def ship_store(ps, group: int, ship: int = None):
    ship = int(ps.miPrimaryShip) if ship is None else ship
    return store_at(get_addressof(ps) + SHIP_STORES[group][1] + ship * STORE_SIZE)


def ship_damage(ps) -> list:
    """Damaged technology in the primary ship's own stores: [(store, id, x, y, damage)]."""
    out = []
    for group, (label, _off) in SHIP_STORES.items():
        for gid, x, y, _amount, dmg, *_ in damaged_tech(ship_store(ps, group)):
            out.append((label, gid, x, y, round(dmg, 3)))
    return out


def full_cost(item) -> bool:
    """The game's own rule (repair popup, RVA 0x69F96D): full cost when the element is not
    FullyInstalled. Repair steps copy the flag from their part, so an installed part that got
    damaged pays DamageRepairFactor x the cost; a part still being installed pays it all."""
    return not item[6]


def _repair_entries(ps):
    """(entry address, (group, ship, x, y)) for every started step repair (RepairTechBuffer).

    In memory: cGcPlayerState+0x182A8 = tk_vector<cGcRepairTechData> {u32 alloc, u32 count, ptr},
    entries 0x1B0 bytes with InventoryIndex +0x1A0, InventorySubIndex +0x1A8, InventoryType +0x1AC."""
    base = get_addressof(ps) + REPAIR_BUFFER
    alloc = ctypes.c_uint32.from_address(base).value
    count = ctypes.c_uint32.from_address(base + 4).value
    ptr = ctypes.c_uint64.from_address(base + 8).value
    if not ptr or count == 0 or count > alloc or count > 256:
        return
    for i in range(count):
        e = ptr + i * REPAIR_ENTRY_SIZE
        entry = map_struct(e, nmse.cGcRepairTechData)
        yield e, (int(entry.InventoryType), int(entry.InventorySubIndex),
                  int(entry.InventoryIndex.X), int(entry.InventoryIndex.Y))


def repairs_in_progress(ps) -> set:
    """(group, ship, x, y) of every part with a started step repair."""
    return {key for _e, key in _repair_entries(ps)}


def started_repairs(ps) -> dict:
    """{(group, ship, x, y): [(step, damaged, installed), ...]} for every started step repair.

    An entry's MaintenanceContainer.InventoryContainer.Slots (cTkDynamicArray at +0x10: pointer,
    u32 count at +0x18) holds the steps "R<i>_<part>", one per requirement i of the part
    (0xEC07E0, 0x10AC9B0); DamageFactor 0 = that step is paid. Seen in a real save: component
    steps are named from a seed (R0_SHIPSL247927), so the step number is all that is used."""
    out = {}
    for e, key in _repair_entries(ps):
        slots = map_struct(e, nmse.cGcRepairTechData).MaintenanceContainer.InventoryContainer.Slots
        steps = []
        if slots.ArrayPointer and 0 < slots.Size <= MAX_STEPS:
            for el in slots:
                gid, _x, _y, _amount, dmg, _typ, installed = row(el)
                m = STEP_ID.match(gid)
                if m:
                    steps.append((int(m.group(1)), dmg > 0, installed))
        out[key] = steps
    return out


def damaged_parts(ps) -> list:
    """Every damaged part of the current ship, in the game's store order:
    (id, group, x, y, installed, steps); steps is None when no step repair was started."""
    primary = int(ps.miPrimaryShip)
    started = started_repairs(ps)
    return [(item[0], group, item[1], item[2], item[6], started.get((group, primary, item[1], item[2])))
            for group in SHIP_STORES for item in damaged_tech(ship_store(ps, group))]


def part_rows(ps) -> dict:
    """{(group, x, y): row} for every damaged part of the current ship (for the REPAIRS grid)."""
    return {(group, r[1], r[2]): r for group in SHIP_STORES for r in damaged_tech(ship_store(ps, group))}


def store_style(ps, group: int) -> tuple:
    """(stack size group, class) of one of the current ship's stores, for a display copy."""
    addr = get_addressof(ship_store(ps, group))
    return ctypes.c_int16.from_address(addr + 0xB8).value, ctypes.c_int32.from_address(addr + 0x100).value


def ship_repair_targets(ps) -> tuple:
    """Damaged parts on the current ship that can be repaired in place, and those that can't.

    Returns (targets, needs_screen). targets are (id, group, x, y, full_cost) for installed
    technology that got damaged, in the ship's general / tech / cargo stores (damaged
    components included), with no step repair started. needs_screen are the ids that must go
    through their repair screen instead:
    - a step repair is already started (repairing in place would pay again for finished steps);
    - the part is not installed yet: RepairTechnology clears the damage but never sets
      FullyInstalled (only the game's finish step 0x10AD8A0 does), so it would be left half done.
    """
    targets, needs_screen = [], []
    for gid, group, x, y, installed, steps in damaged_parts(ps):
        if steps is not None or not installed:
            needs_screen.append(gid)
        else:
            targets.append((gid, group, x, y, not installed))
    return targets, needs_screen


def materials(ps) -> dict:
    """Every non-technology item in the player's own and primary-ship stores: {id: count}."""
    stores = [ps.mInventories[i] for i in range(0x21)] + [ship_store(ps, g) for g in SHIP_STORES]
    totals = {}
    for store in stores:
        for gid, _x, _y, amount, _dmg, typ, *_ in elements(store):
            if typ != TECHNOLOGY and amount > 0:
                totals[gid] = totals.get(gid, 0) + amount
    return totals


def diff(before: dict, after: dict) -> dict:
    keys = set(before) | set(after)
    return {k: after.get(k, 0) - before.get(k, 0) for k in sorted(keys) if after.get(k, 0) != before.get(k, 0)}


def known_stores(ps) -> dict:
    """Address -> label for every store the player state owns (tells a real store from a copy)."""
    base = get_addressof(ps)
    out = {}
    for i in range(0x21):
        name = InventoryChoice(i).name if i in InventoryChoice._value2member_map_ else hex(i)
        out[base + INVENTORIES_ARRAY + i * STORE_SIZE] = f'inventories[{name}]'
    for ship in range(12):
        for _group, (label, off) in SHIP_STORES.items():
            out[base + off + ship * STORE_SIZE] = f'ship[{ship}].{label}'
    return out


def label(ps, address: int) -> str:
    if not address:
        return 'none'
    return known_stores(ps).get(address, f'0x{address:X}')


# ── costs (for showing the plan; the game's own CanRepair still decides) ─────
def repair_factor():
    """The game's DamageRepairFactor (cGcPlayerGlobals, through NMS.py), or None if the globals
    aren't mapped or it doesn't look like a sane multiplier."""
    player = getattr(_globals, 'GcPlayerGlobals', None)
    if player is None or not build_supported():
        return None
    value = float(player.DamageRepairFactor)
    return value if 0.0 < value <= 4.0 else None


_globals = None     # NMS.py's mapped global tables, once load_globals() has run


def load_globals() -> bool:
    """Map the game's global tables with NMS.py (it finds them by name in NMS.exe, ~0.5 s, cached
    by pyMHF afterwards). Imported here, not at the top: nmspy.globals copies pyMHF's base
    address when it is imported. Run once on the game thread, during the loading screen."""
    global _globals
    if _globals is None:
        import nmspy.globals as nms_globals
        nms_globals.globals.instantiate_globals()
        _globals = nms_globals.globals
    return getattr(_globals, 'GcPlayerGlobals', None) is not None and getattr(_globals, 'GcUIGlobals', None) is not None


def tech_requirements(reality) -> dict:
    """{tech id: ((material id, amount), ...)} from the game's own technology table."""
    out = {}
    for tech in reality.mpTechnologyTable.contents.Table:
        gid = str(tech.ID).strip('\x00 ')
        if gid:
            out[gid] = tuple((str(r.ID).strip('\x00 '), int(r.Amount)) for r in tech.Requirements)
    return out


def part_cost(requirements, full: bool, factor):
    """Cost the way RepairTechnology charges it: ceil(amount * (1.0 if full else factor)).
    None when unknown (no requirements known, or the factor is needed but unknown)."""
    if not requirements:
        return None
    if full:
        return tuple((m, a) for m, a in requirements if a > 0)
    if factor is None:
        return None
    return tuple((m, ceil_f32(a, factor)) for m, a in requirements if a > 0)


def ceil_f32(amount: int, factor: float) -> int:
    """ceilf((float)amount * factor) exactly as the game does it in 32-bit floats. The product of
    two float32 values is exact in float64, so rounding it once to float32 matches mulss."""
    f32 = ctypes.c_float(factor).value
    return math.ceil(ctypes.c_float(float(amount) * f32).value)


def ship_plan(ps, requirements: dict, factor) -> core.RepairPlan:
    """What the current ship needs: every part F7 would repair in place, with its cost, against
    what the player holds. The game's CanRepair still makes the real decision per part."""
    targets, _needs_screen = ship_repair_targets(ps)
    slots = [core.Slot(key=(group, x, y), item_id=gid, name=gid,
                       cost=part_cost(requirements.get(gid), full, factor))
             for gid, group, x, y, full in targets]
    return core.plan_repairs(slots, materials(ps))


def remaining_cost(requirements, steps, installed: bool, factor):
    """What is still to pay for a part: all of it (the part's cost rule) when no step repair was
    started, else only its unpaid steps (step i costs requirement i, at the step's own rule).
    None when unknown."""
    if steps is None:
        return part_cost(requirements, not installed, factor)
    if not requirements:
        return None
    out = []
    for n, damaged, step_installed in steps:
        if not damaged:
            continue
        if n >= len(requirements):
            return None
        cost = part_cost(requirements[n:n + 1], not step_installed, factor)
        if cost is None:
            return None
        out.extend(cost)
    return tuple(out)


def repair_board(ps, requirements: dict, factor) -> core.Board:
    """Every damaged part of the current ship with what is left to pay, and the materials all of
    it needs against what the player holds (for the REPAIRS screen's two grids)."""
    parts, in_place = [], set()
    for gid, group, x, y, installed, steps in damaged_parts(ps):
        parts.append(core.Slot(key=(group, x, y), item_id=gid, name=gid,
                               cost=remaining_cost(requirements.get(gid), steps, installed, factor)))
        if steps is None and installed:
            in_place.add((group, x, y))
    return core.repair_board(parts, in_place, materials(ps))


def steps_plan(ps, steps, requirements: dict, factor) -> core.RepairPlan:
    """Same as ship_plan for the open repair screen: each damaged step is its own tech
    (R<i>_<part>) whose requirements are one of the part's, at the step's own cost rule."""
    slots = [core.Slot(key=(x, y), item_id=gid, name=gid,
                       cost=part_cost(requirements.get(gid), full_cost(item), factor))
             for item in damaged_tech(steps) for gid, x, y in [item[:3]]]
    return core.plan_repairs(slots, materials(ps))


def shortfall_text(plan: core.RepairPlan, name=lambda gid: gid, limit: int = 3) -> str:
    """'50 Carbon, 20 Pure Ferrite' - what is still missing to repair everything with a known cost."""
    items = sorted(plan.missing_for_all.items(), key=lambda kv: -kv[1])
    parts = [f'{n} {name(gid)}' for gid, n in items[:limit]]
    if len(items) > limit:
        parts.append(f'+{len(items) - limit} more')
    return ', '.join(parts)


# ── game calls ───────────────────────────────────────────────────────────────
def get_inventory(ps, group: int) -> int:
    inventories = map_struct(get_addressof(ps) + INVENTORIES_OFFSET, nms_ext.cGcPlayerInventories)
    return int(inventories.GetInventory(group, -1) or 0)


def open_repair_steps(ps):
    """The step store of the part whose repair screen is open, or None if no screen is open.

    With no screen open, group 24 falls back to the player's own inventories[0x18]."""
    address = get_inventory(ps, GROUP_REPAIR_STEPS)
    if not address or address in known_stores(ps):
        return None
    store = store_at(address)
    return store if plausible(store) else None


class GameCalls:
    """The two game calls the repair loop needs, bound to one player state."""

    def __init__(self, ps):
        self._fn = map_struct(get_addressof(ps), nms_ext.cGcPlayerState)

    def can_repair(self, store, group, x, y, full_cost):
        idx = nmse.cGcInventoryIndex()
        idx.X, idx.Y = x, y
        return self._fn.CanRepairTechnology(ctypes.byref(store), group, ctypes.byref(idx), full_cost)

    def repair(self, group, x, y, full_cost):
        idx = nmse.cGcInventoryIndex()
        idx.X, idx.Y = x, y
        return self._fn.RepairTechnology(group, ctypes.byref(idx), False, full_cost, False)


def repair_all(read_items, can_repair, repair, key=lambda item: item[:3], read_materials=None,
               max_steps=MAX_STEPS):
    """Repair every damaged item the game says is affordable, one at a time.

    read_items() -> the current damaged items (tuples, id first), re-read after every repair
    since paying can make others unaffordable. can_repair(item) and repair(item) are the game
    calls; each returns True/False, or None when the call itself failed. key(item) identifies an
    item across re-reads. Stops at the first failed or refused call, and if an item the game
    reported as repaired is still damaged (so nothing is ever paid for twice).
    Returns {'repaired': [...ids], 'stopped': reason, 'materials': {id: change}}.
    """
    before = read_materials() if read_materials else {}

    def result(stopped):
        return {'repaired': repaired, 'stopped': stopped,
                'materials': diff(before, read_materials()) if read_materials else {}}

    repaired, done_keys = [], set()
    for _ in range(max_steps):
        items = read_items()
        if not items:
            return result('done')
        target = None
        for item in items:
            if key(item) in done_keys:
                return result(f'{item[0]} still damaged after the game repaired it; stopped')
            answer = can_repair(item)
            if answer is None:
                return result(f'call failed while asking about {item[0]}')
            if answer:
                target = item
                break
        if target is None:
            return result('nothing affordable')
        ok = repair(target)
        if not ok:
            return result(f'repair of {target[0]} ' + ('failed' if ok is None else 'was refused by the game'))
        repaired.append(target[0])
        done_keys.add(key(target))
    return result('safety cap reached')


def repair_open_part(ps, calls, steps, read_materials=None):
    """F7 in a repair screen: every affordable step of that part, with the game's cost rule."""
    group = GROUP_REPAIR_STEPS
    return repair_all(
        read_items=lambda: damaged_tech(steps),
        can_repair=lambda it: calls.can_repair(steps, group, it[1], it[2], full_cost(it)),
        repair=lambda it: calls.repair(group, it[1], it[2], full_cost(it)),
        read_materials=read_materials,
    )


def repair_ship(ps, calls, read_materials=None):
    """F7 anywhere else: every affordable damaged part of the current ship that has no step
    repair in progress, repaired in place on its real inventory, with the game's cost rule."""
    return repair_all(
        read_items=lambda: ship_repair_targets(ps)[0],
        can_repair=lambda t: calls.can_repair(ship_store(ps, t[1]), t[1], t[2], t[3], t[4]),
        repair=lambda t: calls.repair(t[1], t[2], t[3], t[4]),
        key=lambda t: t[:4],
        read_materials=read_materials,
    )
