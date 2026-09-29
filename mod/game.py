"""
Everything that touches No Man's Sky's memory: game function declarations, store readers and
the repair loop. Imported by the NMS.py mod (nmstracker_mod.py); tests import it offline with
PYTEST_VERSION=1 set (pyMHF prompts on import outside a real console otherwise).

Proven in spike S2 (2026-09-29, build 179666): calling RepairTechnology on a repair step pays
the normal materials, repairs the step, and the game saves the result. Function locations come
from a read-only static scan of NMS.exe (work/re); each signature matches exactly once there.

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
from ctypes import c_bool, c_float, c_int32, c_uint32, c_uint64
from ctypes import wintypes
from typing import Annotated

from pymhf.core import _internal
from pymhf.core.hooking import Structure, function_hook
from pymhf.core.memutils import get_addressof, map_struct

from ctypes import _Pointer  # noqa: E402,I001  after pymhf: it swaps in a subscriptable _Pointer on import

import nmspy.data.exported_types as nmse
import nmspy.data.types as nms
from nmspy.data.enums import InventoryChoice

from . import core

GAME_BUILD = 179666
STORE_SIZE = 0x248
INVENTORIES_OFFSET = 0x900      # GetInventory() is called on cGcPlayerState + 0x900
INVENTORIES_ARRAY = 0x910       # cGcPlayerState.mInventories
# Primary-ship stores by the group number GetInventory() uses. NMS.py 180132.0 has
# mShipInventoriesTechOnly at 0xAA28 (wrong) and InventoryChoice 5/6 swapped.
SHIP_STORES = {4: ('general', 0x7458), 5: ('tech', 0xAB28), 6: ('cargo', 0x8FC8)}
GROUP_REPAIR_STEPS = 24         # the steps of the part whose repair screen is open
TECHNOLOGY = 1                  # cGcInventoryType.Technology
MAX_STEPS = 64                  # safety cap for one repair run
REPAIR_BUFFER = 0x182A8         # cGcPlayerState: parts with started step repairs
REPAIR_ENTRY_SIZE = 0x1B0       # sizeof(cGcRepairTechData)

# On-screen message, set up exactly the way the game's own callers do (e.g. RVA 0x3CD216).
# These are RVAs in build 179666 and move with every game update: see running_build().
DATA_GLOBAL = 0x6E7AAE8         # holds the cGcApplication::Data pointer
NOTIFICATIONS_OFFSET = 0x837B40 # Data + this = the player notifications object
MESSAGE_TIME = 0x4B19A78        # float the game passes as display time (1.0)
MESSAGE_COLOUR = 0x51FDD50      # the game's default notification colour
MESSAGE_AUDIO = 0x3C2700B8      # the sound id the game's callers pass
LANGUAGE_MANAGER = 0x6E01550    # what the game's GetLanguageManager (RVA 0x1CB3B0) returns
# cGcPlayerGlobals.DamageRepairFactor (static instance at RVA 0x52368B0, field +0x10A4), filled
# from GCPLAYERGLOBALS.GLOBAL.MBIN at start-up. Discounted cost = ceil(amount * factor).
DAMAGE_REPAIR_FACTOR = 0x5237954


class GameRepair(Structure):
    """Game functions NMS.py does not map yet; call them on map_struct(player_state, GameRepair).

    `this` is typed as a pointer to this class (string annotation) so ctypes accepts the call.
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


class GameInventories(Structure):
    """Lives at cGcPlayerState + 0x900. Only called, never hooked (it has hundreds of callers)."""

    # RVA 0x47BDB0: the store for an inventory group; subIndex -1 means "current ship/vehicle".
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


class GameNotifications(Structure):
    """The player notifications object (Data + 0x837B40). Only called, never hooked."""

    # RVA 0x9B8300 cGcPlayerNotifications::AddTimedMessage. NMS.py 180132.0 lists 10 arguments;
    # build 179666 takes 11 (three trailing bools; the 8th is a float). Read from the game's
    # own 124 call sites, which all fill [rsp+20..50] this way.
    @function_hook("48 8B C4 48 89 58 ? 48 89 70 ? 48 89 78 ? 55 48 8D A8 ? ? ? ? 48 81 EC ? ? ? ? 44 8B 81")
    def AddTimedMessage(
        self,
        this: Annotated[int, c_uint64],
        lsMessage: Annotated[int, c_uint64],        # char buffer (cTkFixedString<512>)
        lfDisplayTime: Annotated[float, c_float],
        lColour: Annotated[int, c_uint64],          # Colour*
        liAudioID: Annotated[int, c_uint32],
        lIcon: Annotated[int, c_uint64],            # cTkSmartResHandle* (int32 0 = no icon)
        lb7: Annotated[bool, c_bool],
        lf8: Annotated[float, c_float],
        lb9: Annotated[bool, c_bool],
        lb10: Annotated[bool, c_bool],
        lb11: Annotated[bool, c_bool],
    ) -> None: ...


class GameLanguage(Structure):
    """The game's language manager. Only called, never hooked."""

    # RVA 0x2BD9570: text key (e.g. "UI_NANOTUBES_NAME", max 31 chars, case-insensitive) ->
    # the translated text in the player's language. The game's callers pass 0 as the third
    # argument (e.g. RVA 0x3BD7B3) and use the returned char* right away.
    @function_hook("48 89 6C 24 ? 48 89 74 24 ? 57 48 83 EC ? 0F 57 C0 49 8B E8")
    def Translate(
        self,
        this: Annotated[int, c_uint64],
        lpacKey: Annotated[int, c_uint64],
        a3: Annotated[int, c_uint64],
    ) -> c_uint64: ...


class Names:
    """Item id -> the name the game shows, in the player's language.

    Keys come from the game's own product / substance / technology tables; text comes from the
    game's own translation. Anything that can't be resolved falls back to the id."""

    def __init__(self):
        self._keys = None
        self._cache = {}

    def load(self, reality) -> int:
        """Read the name keys once from cGcRealityManager. Returns how many were found."""
        keys = {}
        tables = [(reality.mpSubstanceTable, 'Table'), (reality.mpProductTable, 'Table'),
                  (reality.mpTechnologyTable, 'Table')]
        for ptr, field in tables:
            try:
                for entry in getattr(ptr.contents, field):
                    gid = str(entry.ID).strip('\x00 ')
                    # NameLower is the normal-case name ("Chromatic Metal"); Name is capitals.
                    key = str(getattr(entry, 'NameLower', '') or '').strip('\x00 ') or str(entry.Name).strip('\x00 ')
                    if gid and key:
                        keys.setdefault(gid, key)
            except Exception:
                continue
        self._keys = keys
        return len(keys)

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
    raw = ctypes.create_string_buffer(key.encode('ascii', 'ignore')[:31], 32)
    fn = map_struct(base + LANGUAGE_MANAGER, GameLanguage)
    ptr = fn.Translate(ctypes.addressof(raw), 0)
    if not ptr:
        return ''
    return ctypes.string_at(ptr).decode('utf-8', 'replace')      # up to the NUL, like the game reads it


def running_build(path: str = None) -> str:
    """FileVersion string of the running NMS.exe (e.g. '179666'), or '' if it can't be read."""
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
    data = ctypes.c_uint64.from_address(base + DATA_GLOBAL).value
    if not data:
        return False
    this = data + NOTIFICATIONS_OFFSET
    message = ctypes.create_string_buffer(text.encode('utf-8')[:511], 512)
    icon = ctypes.c_int32(0)
    duration = ctypes.c_float.from_address(base + MESSAGE_TIME).value
    fn = map_struct(this, GameNotifications)
    fn.AddTimedMessage(ctypes.addressof(message), duration, base + MESSAGE_COLOUR, MESSAGE_AUDIO,
                       ctypes.addressof(icon), False, 0.0, False, False, False)
    return True


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


def repairs_in_progress(ps) -> set:
    """(group, ship, x, y) of every part with a started step repair (PlayerStateData.RepairTechBuffer).

    In memory: cGcPlayerState+0x182A8 = tk_vector<cGcRepairTechData> {u32 alloc, u32 count, ptr},
    entries 0x1B0 bytes with InventoryIndex +0x1A0, InventorySubIndex +0x1A8, InventoryType +0x1AC."""
    base = get_addressof(ps) + REPAIR_BUFFER
    alloc = ctypes.c_uint32.from_address(base).value
    count = ctypes.c_uint32.from_address(base + 4).value
    ptr = ctypes.c_uint64.from_address(base + 8).value
    if not ptr or count == 0 or count > alloc or count > 256:
        return set()
    out = set()
    for i in range(count):
        e = ptr + i * REPAIR_ENTRY_SIZE
        x = ctypes.c_int32.from_address(e + 0x1A0).value
        y = ctypes.c_int32.from_address(e + 0x1A4).value
        ship = ctypes.c_int32.from_address(e + 0x1A8).value
        group = ctypes.c_int32.from_address(e + 0x1AC).value
        out.add((group, ship, x, y))
    return out


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
    primary = int(ps.miPrimaryShip)
    started = repairs_in_progress(ps)
    targets, needs_screen = [], []
    for group in SHIP_STORES:
        for item in damaged_tech(ship_store(ps, group)):
            gid, x, y, installed = item[0], item[1], item[2], item[6]
            if (group, primary, x, y) in started or not installed:
                needs_screen.append(gid)
            else:
                targets.append((gid, group, x, y, full_cost(item)))
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
    """The game's DamageRepairFactor, or None if it doesn't look like a sane multiplier."""
    base = _internal.BASE_ADDRESS
    if base in (None, -1) or not build_supported():
        return None
    value = ctypes.c_float.from_address(base + DAMAGE_REPAIR_FACTOR).value
    return value if 0.0 < value <= 4.0 else None


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
    return int(map_struct(get_addressof(ps) + INVENTORIES_OFFSET, GameInventories).GetInventory(group, -1) or 0)


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
        self._fn = map_struct(get_addressof(ps), GameRepair)

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
