"""Offline tests for mod/game.py and the mod entry file.

No game runs here. Stores are built in ctypes buffers laid out like game memory, and game
calls are intercepted just before they would jump into NMS.exe: the test runs ctypes'
own argument conversion on the exact arguments (where the in-game ArgumentError came from)
and answers like the game would.
"""
import ctypes
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('PYTEST_VERSION', '1')      # pyMHF prompts on import outside a real console
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from pymhf.core.functions import _get_funcdef
    from pymhf.core.hooking import FunctionHook

    from mod import game
    HAVE_NMSPY = True
except ImportError:                                # pragma: no cover - NMS.py not installed
    HAVE_NMSPY = False

PS_SIZE = 0x18400


def addr(x):
    """The address behind an argument pyMHF passed: byref(struct) for `this`, a typed pointer, or
    a plain int."""
    if hasattr(x, '_obj'):
        return ctypes.addressof(x._obj)
    if isinstance(x, int):
        return x
    return ctypes.cast(x, ctypes.c_void_p).value          # pyMHF swaps ctypes._Pointer, so no isinstance


def make_store(buf_addr, rows, width=10, height=2, capacity=20):
    """Write a cGcInventoryStore at buf_addr holding rows of (id, x, y, amount, damage, type[, installed])."""
    arr = (game.nmse.cGcInventoryElement * max(len(rows), 1))()
    for el, r in zip(arr, rows):
        gid, x, y, amount, dmg, typ = r[:6]
        installed = r[6] if len(r) > 6 else False
        a = ctypes.addressof(el)
        ctypes.memmove(a, gid.encode().ljust(0x10, b'\0'), 0x10)
        ctypes.c_int32.from_address(a + 0x10).value = x
        ctypes.c_int32.from_address(a + 0x14).value = y
        ctypes.c_int32.from_address(a + 0x18).value = amount
        ctypes.c_float.from_address(a + 0x1C).value = dmg
        ctypes.c_uint32.from_address(a + 0x24).value = typ
        ctypes.c_uint8.from_address(a + 0x29).value = int(installed)
    ctypes.c_int16.from_address(buf_addr + 0x80).value = width
    ctypes.c_int16.from_address(buf_addr + 0x82).value = height
    ctypes.c_int16.from_address(buf_addr + 0x84).value = capacity
    ctypes.c_uint32.from_address(buf_addr + 0x88).value = len(rows)
    ctypes.c_uint32.from_address(buf_addr + 0x8C).value = len(rows)
    ctypes.c_uint64.from_address(buf_addr + 0x90).value = ctypes.addressof(arr)
    return arr      # keep alive


def make_repair_buffer(ps_addr, entries):
    """Write cGcPlayerState.RepairTechBuffer with entries of (group, ship, x, y[, steps]);
    steps are (id, damage, installed) written as the entry's InventoryContainer.Slots."""
    arr = ctypes.create_string_buffer(max(len(entries), 1) * game.REPAIR_ENTRY_SIZE)
    keep = [arr]
    for i, entry in enumerate(entries):
        group, ship, x, y = entry[:4]
        e = ctypes.addressof(arr) + i * game.REPAIR_ENTRY_SIZE
        ctypes.c_int32.from_address(e + 0x1A0).value = x
        ctypes.c_int32.from_address(e + 0x1A4).value = y
        ctypes.c_int32.from_address(e + 0x1A8).value = ship
        ctypes.c_int32.from_address(e + 0x1AC).value = group
        steps = entry[4] if len(entry) > 4 else []
        if steps:
            els = (game.nmse.cGcInventoryElement * len(steps))()
            for k, (sid, dmg, installed) in enumerate(steps):
                a = ctypes.addressof(els[k])
                ctypes.memmove(a, sid.encode().ljust(0x10, b'\0'), 0x10)
                ctypes.c_int32.from_address(a + 0x10).value = k
                ctypes.c_float.from_address(a + 0x1C).value = dmg
                ctypes.c_uint32.from_address(a + 0x24).value = 1
                ctypes.c_uint8.from_address(a + 0x29).value = int(installed)
            ctypes.c_uint64.from_address(e + 0x10).value = ctypes.addressof(els)
            ctypes.c_uint32.from_address(e + 0x18).value = len(steps)
            keep.append(els)
    base = ps_addr + game.REPAIR_BUFFER
    ctypes.c_uint32.from_address(base).value = len(entries)
    ctypes.c_uint32.from_address(base + 4).value = len(entries)
    ctypes.c_uint64.from_address(base + 8).value = ctypes.addressof(arr)
    return keep


class FakePlayer:
    """A fake cGcPlayerState: primary ship 1 with general and tech stores."""

    def __init__(self, general, tech, personal=(), in_progress=()):
        self.buf = ctypes.create_string_buffer(PS_SIZE)
        self.addr = ctypes.addressof(self.buf)
        ctypes.c_uint32.from_address(self.addr + 0x182A0).value = 1
        self.keep = [
            make_store(self.addr + 0x7458 + 0x248, list(general)),
            make_store(self.addr + 0xAB28 + 0x248, list(tech)),
            make_store(self.addr + 0x910, list(personal)),
            make_repair_buffer(self.addr, list(in_progress)),
        ]
        self.ps = game.nms.cGcPlayerState.from_address(self.addr)


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.p = FakePlayer(
            general=[('FUEL1', 0, 0, 50, 0.0, 0), ('SPACEGUNK5', 1, 0, 3, 1.0, 0), ('SHIPSLOT_DMG4', 5, 1, 0, 1.0, 1, True)],
            tech=[('LAUNCHER', 0, 0, 0, 1.0, 1, True), ('HYPERDRIVE', 1, 0, 100, 0.0, 1, True),
                  ('SHIPROCKETS', 2, 1, 0, 1.0, 1, False), ('SHIPJUMP1', 3, 0, 0, 1.0, 1, True)],
            personal=[('CARBON_SEAL', 0, 0, 2, 0.0, 2), ('FUEL1', 1, 0, 10, 0.0, 0)],
            in_progress=[(5, 1, 3, 0)],          # SHIPJUMP1 has started steps
        )
        self.ps = self.p.ps

    def test_elements_carry_installed_flag(self):
        rows = game.elements(game.ship_store(self.ps, 5))
        self.assertEqual([(r[0], r[6]) for r in rows],
                         [('LAUNCHER', True), ('HYPERDRIVE', True), ('SHIPROCKETS', False), ('SHIPJUMP1', True)])

    def test_ship_damage_ignores_junk(self):
        self.assertEqual([d[1] for d in game.ship_damage(self.ps)], ['SHIPSLOT_DMG4', 'LAUNCHER', 'SHIPROCKETS', 'SHIPJUMP1'])

    def test_materials_sum_across_stores(self):
        self.assertEqual(game.materials(self.ps), {'FUEL1': 60, 'SPACEGUNK5': 3, 'CARBON_SEAL': 2})

    def test_cost_rule(self):
        self.assertFalse(game.full_cost(('R0_SHIPJUMP1', 0, 0, 0, 1.0, 1, True)))   # damaged installed part
        self.assertTrue(game.full_cost(('R0_SCANBINOC1', 0, 0, 0, 1.0, 1, False)))  # part being installed

    def test_repairs_in_progress(self):
        self.assertEqual(game.repairs_in_progress(self.ps), {(5, 1, 3, 0)})

    def test_repairs_in_progress_rejects_garbage(self):
        base = self.p.addr + game.REPAIR_BUFFER
        ctypes.c_uint32.from_address(base).value = 2
        ctypes.c_uint32.from_address(base + 4).value = 900     # count > alloc: don't follow
        self.assertEqual(game.repairs_in_progress(self.ps), set())

    def test_ship_targets_split(self):
        targets, needs_screen = game.ship_repair_targets(self.ps)
        self.assertEqual([t[:4] for t in targets], [('SHIPSLOT_DMG4', 4, 5, 1), ('LAUNCHER', 5, 0, 0)])
        self.assertTrue(all(t[4] is False for t in targets))           # installed -> discounted cost
        self.assertEqual(sorted(needs_screen), ['SHIPJUMP1', 'SHIPROCKETS'])

    def test_implausible_store_is_not_followed(self):
        bad = ctypes.create_string_buffer(0x248)
        ctypes.c_int16.from_address(ctypes.addressof(bad) + 0x80).value = 500     # impossible width
        ctypes.c_uint64.from_address(ctypes.addressof(bad) + 0x90).value = 0xDEAD  # would crash if read
        ctypes.c_uint32.from_address(ctypes.addressof(bad) + 0x8C).value = 3
        self.assertEqual(game.elements(game.store_at(ctypes.addressof(bad))), [])

    def test_labels(self):
        self.assertEqual(game.label(self.ps, self.p.addr + 0xAB28 + 0x248), 'ship[1].tech')
        self.assertEqual(game.label(self.ps, self.p.addr + 0x910 + 0x18 * 0x248), 'inventories[Unknown0x18]')

    def test_open_repair_steps(self):
        steps_buf = ctypes.create_string_buffer(0x248)
        keep = make_store(ctypes.addressof(steps_buf), [('R0_SHIPJUMP1', 0, 0, 0, 1.0, 1, True)])  # noqa: F841
        original = game.get_inventory
        try:
            game.get_inventory = lambda ps, group: ctypes.addressof(steps_buf)
            self.assertIsNotNone(game.open_repair_steps(self.ps))
            game.get_inventory = lambda ps, group: self.p.addr + 0x910 + 0x18 * 0x248   # fallback store
            self.assertIsNone(game.open_repair_steps(self.ps))
            game.get_inventory = lambda ps, group: 0
            self.assertIsNone(game.open_repair_steps(self.ps))
        finally:
            game.get_inventory = original


class GameCallTestBase(unittest.TestCase):
    """Intercepts pyMHF game calls: checks ctypes accepts the exact args, then answers."""

    def setUp(self):
        if not HAVE_NMSPY:
            self.skipTest('NMS.py not installed')
        self.calls, self.this = [], []
        self._orig = FunctionHook._call
        test = self

        def fake_call(hook, *args, **kwargs):
            fd = _get_funcdef(hook._func)
            flat = fd.flatten(*args, **kwargs)
            for argtype, arg in zip(fd.arg_types, flat):
                argtype.from_param(arg)
            test.calls.append((hook._func.__name__, flat[1:]))
            test.this.append(flat[0] if flat else None)
            return test.answer(hook._func.__name__, flat)
        FunctionHook._call = fake_call

    def answer(self, name, args):
        return True

    def tearDown(self):
        FunctionHook._call = self._orig


class CallArgumentsTest(GameCallTestBase):
    def setUp(self):
        super().setUp()
        self.buf = ctypes.create_string_buffer(PS_SIZE)
        self.ps = game.nms.cGcPlayerState.from_address(ctypes.addressof(self.buf))

    def test_can_repair_and_repair(self):
        steps_buf = ctypes.create_string_buffer(0x248)
        calls = game.GameCalls(self.ps)
        self.assertTrue(calls.can_repair(game.store_at(ctypes.addressof(steps_buf)), 24, 1, 0, False))
        self.assertTrue(calls.repair(24, 1, 0, False))
        self.assertEqual([c[0] for c in self.calls], ['CanRepairTechnology', 'RepairTechnology'])
        self.assertEqual(self.calls[1][1][2:], [False, False, False])   # never free, never repair kit

    def test_get_inventory(self):
        self.assertEqual(game.get_inventory(self.ps, 24), 1)

    def test_show_message_builds_the_games_arguments(self):
        base = 0x140000000
        data = ctypes.create_string_buffer(game.NOTIFICATIONS_OFFSET + 0x400)
        ui = game.nmse.cGcUIGlobals.from_buffer(bytearray(ctypes.sizeof(game.nmse.cGcUIGlobals)))
        orig = (game._internal.BASE_ADDRESS, game.build_supported, game.gameData.GcApplication, game._globals)
        try:
            game._internal.BASE_ADDRESS = base
            game.build_supported = lambda: True
            game.gameData.GcApplication = SimpleNamespace(mpData=ctypes.c_void_p(ctypes.addressof(data)))
            game._globals = None
            self.assertFalse(game.show_message('no globals mapped yet'))
            game._globals = SimpleNamespace(GcUIGlobals=ui)
            self.assertTrue(game.show_message('NMS Tracker: repaired 2 step(s)'))
        finally:
            (game._internal.BASE_ADDRESS, game.build_supported, game.gameData.GcApplication, game._globals) = orig
        name, args = self.calls[-1]
        self.assertEqual(name, 'AddTimedMessage')
        self.assertEqual(addr(self.this[-1]), ctypes.addressof(data) + game.NOTIFICATIONS_OFFSET)
        self.assertEqual(len(args), 10)                            # 11 including `this`
        self.assertEqual(args[1], 1.0)                             # the game's own display time
        self.assertEqual(addr(args[2]), ctypes.addressof(ui) + 0x5640)  # GcUIGlobals.HUDWarningColour (NMS.py)
        self.assertEqual(game.nmse.cGcUIGlobals.HUDWarningColour.offset, 0x5640)
        self.assertEqual(args[3], game.MESSAGE_AUDIO)

    def test_show_message_refuses_other_builds(self):
        orig = game.build_supported
        saved = game._internal.BASE_ADDRESS
        try:
            game._internal.BASE_ADDRESS = 0x140000000
            game.build_supported = lambda: False
            self.assertFalse(game.show_message('x'))
        finally:
            game.build_supported = orig
            game._internal.BASE_ADDRESS = saved
        self.assertEqual(self.calls, [])


class NamesTest(GameCallTestBase):
    def setUp(self):
        super().setUp()
        self.text = ctypes.create_string_buffer('Carbon Nanotubes'.encode())
        self.lang = ctypes.create_string_buffer(0x100)

    def answer(self, name, args):
        if name == 'GetInstance':                            # cTkLanguageManager (static)
            return ctypes.addressof(self.lang)
        if name == 'Translate_internal':                     # NMS.py's cTkLanguageManagerBase
            self.key_seen = ctypes.string_at(args[1])
            return ctypes.addressof(self.text)
        return True

    def reality(self):
        from types import SimpleNamespace as NS

        def table(rows):
            return NS(contents=NS(Table=[NS(ID=gid, Name=key) for gid, key in rows]))
        return NS(mpSubstanceTable=table([('FUEL1', 'UI_FUEL_1_NAME')]),
                  mpProductTable=table([('NANOTUBES', 'UI_NANOTUBES_NAME')]),
                  mpTechnologyTable=table([('LAUNCHER', 'SHIP_LAUNCHER_NAME')]))

    def test_load_reads_all_three_tables(self):
        names = game.Names()
        self.assertEqual(names.load(self.reality()), 3)
        self.assertEqual([names.kind(g) for g in ('FUEL1', '^NANOTUBES', 'LAUNCHER', 'NOPE')],
                         [game.SUBSTANCE, game.PRODUCT, game.TECHNOLOGY, None])

    def test_prefers_the_normal_case_name_key(self):
        from types import SimpleNamespace as NS
        entry = NS(ID='STELLAR2', Name='UI_STELLAR2_NAME', NameLower='UI_STELLAR2_NAME_L')
        empty = NS(contents=NS(Table=[]))
        names = game.Names()
        names.load(NS(mpSubstanceTable=NS(contents=NS(Table=[entry])), mpProductTable=empty, mpTechnologyTable=empty))
        seen = []
        names.name('STELLAR2', translate=lambda k: seen.append(k) or 'Chromatic Metal')
        self.assertEqual(seen, ['UI_STELLAR2_NAME_L'])

    def test_translate_key_uses_the_games_language_manager(self):
        orig_base, orig_supported = game._internal.BASE_ADDRESS, game.build_supported
        try:
            game._internal.BASE_ADDRESS = 0x140000000
            game.build_supported = lambda: True
            self.assertEqual(game.translate_key('UI_NANOTUBES_NAME'), 'Carbon Nanotubes')
        finally:
            game._internal.BASE_ADDRESS, game.build_supported = orig_base, orig_supported
        self.assertEqual(self.key_seen, b'UI_NANOTUBES_NAME')
        name, args = self.calls[-1]
        self.assertEqual((name, args[1]), ('Translate_internal', None))    # no fallback text
        self.assertEqual(addr(self.this[-1]), ctypes.addressof(self.lang))  # on the game's language manager

    def test_name_translates_and_falls_back(self):
        names = game.Names()
        names.load(self.reality())
        self.assertEqual(names.name('^NANOTUBES', translate=lambda k: 'Carbon Nanotubes'), 'Carbon Nanotubes')
        self.assertEqual(names.name('UNKNOWN_ID', translate=lambda k: 'x'), 'UNKNOWN_ID')     # no key
        self.assertEqual(names.name('FUEL1', translate=lambda k: ''), 'FUEL1')                # empty text
        self.assertEqual(game.Names().name('LAUNCHER'), 'LAUNCHER')                           # never loaded

    def test_translate_failure_falls_back(self):
        names = game.Names()
        names.load(self.reality())

        def boom(key):
            raise OSError('no')
        self.assertEqual(names.name('LAUNCHER', translate=boom), 'LAUNCHER')


class RepairShipTest(GameCallTestBase):
    """repair_ship end to end on fake memory; the fake RepairTechnology clears the slot's
    damage the way the real one does, so the loop's re-reads see real progress."""

    def setUp(self):
        super().setUp()
        self.p = FakePlayer(
            general=[('SHIPSLOT_DMG4', 5, 1, 0, 1.0, 1, True), ('SHIPSLOT_DMG9', 4, 4, 0, 1.0, 1, True)],
            tech=[('LAUNCHER', 0, 0, 0, 1.0, 1, True), ('SHIPROCKETS', 2, 1, 0, 1.0, 1, False)],
        )
        self.affordable = {('SHIPSLOT_DMG4', 4), ('LAUNCHER', 5), ('SHIPSLOT_DMG9', 4)}

    def element(self, group, x, y):
        store = game.ship_store(self.p.ps, group)
        for el in store.mStore:
            if (int(el.Index.X), int(el.Index.Y)) == (x, y):
                return el

    def answer(self, name, args):
        if name == 'CanRepairTechnology':
            store_addr = ctypes.addressof(args[1]._obj)
            group = args[2]
            idx = args[3]._obj
            gid = next(r[0] for r in game.elements(game.store_at(store_addr)) if (r[1], r[2]) == (idx.X, idx.Y))
            return (gid, group) in self.affordable
        if name == 'RepairTechnology':
            group, idx = args[1], args[2]._obj
            self.element(group, idx.X, idx.Y).DamageFactor = 0.0
            return True
        return True

    def test_repairs_affordable_installed_parts_in_place(self):
        r = game.repair_ship(self.p.ps, game.GameCalls(self.p.ps))
        self.assertEqual(r['repaired'], ['SHIPSLOT_DMG4', 'SHIPSLOT_DMG9', 'LAUNCHER'])
        self.assertEqual(r['stopped'], 'done')
        repairs = [c for c in self.calls if c[0] == 'RepairTechnology']
        self.assertEqual([(c[1][0], c[1][1]._obj.X, c[1][1]._obj.Y) for c in repairs], [(4, 5, 1), (4, 4, 4), (5, 0, 0)])
        self.assertTrue(all(c[1][2:] == [False, False, False] for c in repairs))   # installed -> discounted
        self.assertEqual([d[1] for d in game.ship_damage(self.p.ps)], ['SHIPROCKETS'])  # left for its screen

    def test_skips_unaffordable_and_stops(self):
        self.affordable = {('LAUNCHER', 5)}
        r = game.repair_ship(self.p.ps, game.GameCalls(self.p.ps))
        self.assertEqual(r['repaired'], ['LAUNCHER'])
        self.assertEqual(r['stopped'], 'nothing affordable')

    def test_repair_that_does_not_stick_is_not_paid_twice(self):
        self.answer = lambda name, args: True      # "succeeds" but never clears the damage
        r = game.repair_ship(self.p.ps, game.GameCalls(self.p.ps))
        self.assertEqual(r['repaired'], ['SHIPSLOT_DMG4'])
        self.assertIn('still damaged', r['stopped'])
        self.assertEqual(sum(1 for c in self.calls if c[0] == 'RepairTechnology'), 1)


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class CostTest(unittest.TestCase):
    def test_ceil_matches_32_bit_game_maths(self):
        self.assertEqual(game.ceil_f32(100, 0.7), 70)       # float64 would give 70.00000000000001 -> 71
        self.assertEqual(game.ceil_f32(300, 0.5), 150)
        self.assertEqual(game.ceil_f32(1, 0.5), 1)
        self.assertEqual(game.ceil_f32(3, 0.1), 1)
        self.assertEqual(game.ceil_f32(250, 1.0), 250)

    def test_part_cost(self):
        reqs = (('LUSH1', 300), ('ROBOT1', 20), ('NOTHING', 0))
        self.assertEqual(game.part_cost(reqs, True, None), (('LUSH1', 300), ('ROBOT1', 20)))
        self.assertEqual(game.part_cost(reqs, False, 0.5), (('LUSH1', 150), ('ROBOT1', 10)))
        self.assertIsNone(game.part_cost(reqs, False, None))      # discounted but factor unknown
        self.assertIsNone(game.part_cost((), True, 0.5))           # no requirements known

    def test_tech_requirements_from_game_table(self):
        from types import SimpleNamespace as NS
        tech = NS(ID='SHIPSLOT_DMG4', Requirements=[NS(ID='LUSH1', Amount=300), NS(ID='ROBOT1', Amount=20)])
        reality = NS(mpTechnologyTable=NS(contents=NS(Table=[tech, NS(ID='', Requirements=[])])))
        self.assertEqual(game.tech_requirements(reality), {'SHIPSLOT_DMG4': (('LUSH1', 300), ('ROBOT1', 20))})

    def test_ship_plan_and_shortfall(self):
        p = FakePlayer(
            general=[('SHIPSLOT_DMG4', 5, 1, 0, 1.0, 1, True), ('SHIPSLOT_DMG9', 4, 4, 0, 1.0, 1, True),
                     ('LUSH1', 0, 0, 200, 0.0, 0)],
            tech=[('SHIPROCKETS', 2, 1, 0, 1.0, 1, False)],          # needs its screen: not in the plan
            personal=[('EX_YELLOW', 0, 0, 100, 0.0, 0), ('ROBOT1', 1, 0, 5, 0.0, 2)],
        )
        reqs = {'SHIPSLOT_DMG4': (('LUSH1', 300), ('ROBOT1', 20)), 'SHIPSLOT_DMG9': (('EX_YELLOW', 150),)}
        plan = game.ship_plan(p.ps, reqs, 0.5)
        self.assertEqual([s.slot.item_id for s in plan.slots], ['SHIPSLOT_DMG4', 'SHIPSLOT_DMG9'])
        self.assertEqual([s.item_id for s in plan.batch], ['SHIPSLOT_DMG9'])         # 75 EX_YELLOW: have 100
        self.assertEqual(plan.slots[0].short_by, {'ROBOT1': 5})                     # needs 150 LUSH1 + 10 ROBOT1
        names = {'ROBOT1': 'Pugneum'}
        self.assertEqual(game.shortfall_text(plan, lambda g: names.get(g, g)), '5 Pugneum')

    def test_steps_plan_uses_each_steps_own_cost_rule(self):
        p = FakePlayer(general=[], tech=[], personal=[('LAND1', 0, 0, 30, 0.0, 0)])
        steps_buf = ctypes.create_string_buffer(0x248)
        keep = make_store(ctypes.addressof(steps_buf), [  # noqa: F841
            ('R0_BOLT', 0, 0, 0, 1.0, 1, False),        # being installed -> full cost
            ('R1_BOLT', 1, 0, 0, 0.0, 1, False),        # already done -> not in the plan
        ])
        steps = game.store_at(ctypes.addressof(steps_buf))
        plan = game.steps_plan(p.ps, steps, {'R0_BOLT': (('LAND1', 50),)}, 0.5)
        self.assertEqual([s.slot.item_id for s in plan.slots], ['R0_BOLT'])
        self.assertEqual(plan.slots[0].short_by, {'LAND1': 20})                  # full 50, have 30
        self.assertEqual(game.shortfall_text(plan, {'LAND1': 'Ferrite Dust'}.get), '20 Ferrite Dust')

    def test_repair_factor_reads_the_game_value_and_rejects_nonsense(self):
        player = game.nmse.cGcPlayerGlobals.from_buffer(bytearray(ctypes.sizeof(game.nmse.cGcPlayerGlobals)))
        orig_globals, orig_supported = game._globals, game.build_supported
        try:
            game.build_supported = lambda: True
            game._globals = None
            self.assertIsNone(game.repair_factor())                        # NMS.py globals not mapped yet
            game._globals = SimpleNamespace(GcPlayerGlobals=player)        # as nmspy.globals maps them
            player.DamageRepairFactor = 0.5
            self.assertEqual(game.repair_factor(), 0.5)
            player.DamageRepairFactor = 0.0                                # not loaded yet
            self.assertIsNone(game.repair_factor())
            player.DamageRepairFactor = 1e9
            self.assertIsNone(game.repair_factor())
        finally:
            game._globals, game.build_supported = orig_globals, orig_supported


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class BoardTest(unittest.TestCase):
    """Remaining costs, shaped like save slot 1 on 2026-09-30: a component with a started step
    repair (one step paid), an untouched component, a part being installed with a started repair,
    and an affordable installed part."""
    REQ = {'SHIPSLOT_DMG4': (('LUSH1', 300), ('ROBOT1', 20)), 'SHIPSLOT_DMG9': (('COPPER', 150),),
           'SHIPROCKETS': (('FUEL1', 100), ('LAND1', 50)), 'LAUNCHER': (('FUEL1', 20),)}

    def setUp(self):
        self.p = FakePlayer(
            general=[('SHIPSLOT_DMG4', 5, 1, 1, 1.0, 1, True), ('SHIPSLOT_DMG9', 4, 4, 3, 1.0, 1, True)],
            tech=[('SHIPROCKETS', 2, 1, -1, 1.0, 1, False), ('LAUNCHER', 0, 0, 0, 1.0, 1, True)],
            personal=[('FUEL1', 0, 0, 60, 0.0, 0)],
            in_progress=[(4, 1, 5, 1, [('R0_SHIPSL247927', 1.0, True), ('R1_SHIPSL247927', 0.0, True)]),
                         (5, 1, 2, 1, [('R0_SHIPROCKETS', 1.0, False), ('R1_SHIPROCKETS', 0.0, False)])],
        )

    def test_started_repairs_read_steps(self):
        self.assertEqual(game.started_repairs(self.p.ps), {
            (4, 1, 5, 1): [(0, True, True), (1, False, True)],
            (5, 1, 2, 1): [(0, True, False), (1, False, False)],
        })

    def test_targets_unchanged(self):
        targets, needs_screen = game.ship_repair_targets(self.p.ps)
        self.assertEqual([t[:4] for t in targets], [('SHIPSLOT_DMG9', 4, 4, 4), ('LAUNCHER', 5, 0, 0)])
        self.assertEqual(needs_screen, ['SHIPSLOT_DMG4', 'SHIPROCKETS'])

    def test_remaining_cost(self):
        req = self.REQ['SHIPSLOT_DMG4']
        self.assertEqual(game.remaining_cost(req, None, True, 0.5), (('LUSH1', 150), ('ROBOT1', 10)))
        self.assertEqual(game.remaining_cost(req, None, False, 0.5), (('LUSH1', 300), ('ROBOT1', 20)))
        self.assertEqual(game.remaining_cost(req, [(0, True, True), (1, False, True)], True, 0.5), (('LUSH1', 150),))
        self.assertEqual(game.remaining_cost(req, [(0, False, True), (1, False, True)], True, 0.5), ())
        self.assertIsNone(game.remaining_cost(req, [(5, True, True)], True, 0.5))    # no such requirement
        self.assertIsNone(game.remaining_cost(None, [(0, True, True)], True, 0.5))
        self.assertIsNone(game.remaining_cost(req, [(0, True, True)], True, None))   # factor unknown

    def test_board(self):
        board = game.repair_board(self.p.ps, self.REQ, 0.5)
        self.assertEqual([(b.slot.item_id, b.how, b.cost) for b in board.parts], [
            ('LAUNCHER', 'now', {'FUEL1': 10}),
            ('SHIPSLOT_DMG9', 'materials', {'COPPER': 75}),
            ('SHIPSLOT_DMG4', 'screen', {'LUSH1': 150}),
            ('SHIPROCKETS', 'screen', {'FUEL1': 100}),
        ])
        self.assertEqual([(m.item_id, m.needed, m.have, m.missing) for m in board.materials], [
            ('LUSH1', 150, 0, 150), ('COPPER', 75, 0, 75), ('FUEL1', 110, 60, 50)])
        self.assertEqual(board.unknown, 0)


class RepairLoopTest(unittest.TestCase):
    """repair_all decisions with plain fake callables."""

    def setUp(self):
        if not HAVE_NMSPY:
            self.skipTest('NMS.py not installed')
        self.damaged = {(0, 0): 'R0_A', (1, 0): 'R1_A', (2, 0): 'R2_A'}
        self.affordable = {(0, 0), (1, 0), (2, 0)}
        self.stock = {'SEAL': 5}

    def read(self):
        return [(gid, x, y) for (x, y), gid in sorted(self.damaged.items())]

    def can(self, it):
        return (it[1], it[2]) in self.affordable

    def repair(self, it):
        del self.damaged[(it[1], it[2])]
        self.stock['SEAL'] -= 1
        return True

    def run_loop(self, **kw):
        return game.repair_all(kw.get('read', self.read), kw.get('can', self.can), kw.get('repair', self.repair),
                               read_materials=lambda: dict(self.stock), max_steps=kw.get('cap', 64))

    def test_repairs_everything_affordable(self):
        r = self.run_loop()
        self.assertEqual((r['repaired'], r['stopped'], r['materials']), (['R0_A', 'R1_A', 'R2_A'], 'done', {'SEAL': -3}))

    def test_skips_unaffordable_first_step(self):
        self.affordable = {(2, 0)}
        r = self.run_loop()
        self.assertEqual((r['repaired'], r['stopped']), (['R2_A'], 'nothing affordable'))

    def test_nothing_affordable(self):
        self.affordable = set()
        self.assertEqual(self.run_loop()['stopped'], 'nothing affordable')

    def test_failed_ask_stops_without_repairing(self):
        r = self.run_loop(can=lambda it: None)
        self.assertEqual(r['repaired'], [])
        self.assertIn('call failed', r['stopped'])
        self.assertEqual(len(self.damaged), 3)

    def test_refused_and_failed_repairs_stop(self):
        self.assertIn('refused', self.run_loop(repair=lambda it: False)['stopped'])
        self.assertIn('failed', self.run_loop(repair=lambda it: None)['stopped'])

    def test_safety_cap(self):
        counter = iter(range(1000))
        r = self.run_loop(read=lambda: [(f'N{next(counter)}', 9, 9)], can=lambda it: True,
                          repair=lambda it: True, cap=5)       # an endless stream of new items
        self.assertEqual((len(r['repaired']), r['stopped']), (5, 'safety cap reached'))

    def test_nothing_damaged(self):
        self.damaged = {}
        self.assertEqual(self.run_loop()['stopped'], 'done')


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class BuildTest(unittest.TestCase):
    EXE = r"E:\SteamLibrary\steamapps\common\No Man's Sky\Binaries\NMS.exe"

    @unittest.skipUnless(os.path.exists(EXE), 'game not installed here')
    def test_reads_the_installed_game_build(self):
        self.assertEqual(game.running_build(self.EXE), str(game.GAME_BUILD))

    def test_unreadable_path(self):
        self.assertEqual(game.running_build(r'C:\no\such\file.exe'), '')


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class ModEntryTest(unittest.TestCase):
    def load(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('nmstracker_mod', ROOT / 'mod' / 'nmstracker_mod.py')
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_mod_loads_with_hook_key_and_frame_callback_and_no_window(self):
        m = self.load()
        mod = m.NMSTrackerMod()
        self.assertEqual(sorted((h._hook_func_name, h._hook_time.name) for h in mod.hooks), [
            ('cGcFrontendManager.Activate', 'AFTER'), ('cGcFrontendManager.Activate', 'BEFORE'),      # NMS.py
            ('cGcFrontendManager.PrevNextPage', 'AFTER'), ('cGcFrontendManager.PrevNextPage', 'BEFORE'),
            ('cGcFrontendPageFunctions.DoInventory', 'BEFORE'),
            ('cGcFrontendPageFunctions.DoToolbar', 'AFTER'), ('cGcFrontendPageFunctions.DoToolbar', 'BEFORE'),  # NMS.py
            ('cGcPlayerState.CanRepairTechnology', 'AFTER'),
        ])
        self.assertFalse(mod.tab.enabled)            # off until the game build is checked
        self.assertEqual([f._hotkey for f in mod._hotkey_funcs], ['f7'])
        self.assertEqual(len(mod._gui_widgets), 0)
        self.assertTrue(getattr(m.NMSTrackerMod, '_no_gui', False))
        self.assertEqual(len(mod._custom_callbacks), 1)

    def test_hooks_hand_the_game_its_own_this_back(self):
        m = self.load()
        ptr = ctypes.pointer(ctypes.c_uint64(5))
        self.assertIsNone(m.with_this(ptr, None))
        self.assertEqual(m.with_this(ptr, (0x1234, 1, True)), (ptr, 1, True))

    def test_screen_panel_from_game_memory(self):
        m = self.load()
        mod = m.NMSTrackerMod()
        p = FakePlayer(
            general=[('SHIPSLOT_DMG4', 5, 1, 0, 1.0, 1, True), ('SHIPSLOT_DMG9', 4, 4, 0, 1.0, 1, True),
                     ('LUSH1', 0, 0, 200, 0.0, 0)],
            tech=[('SHIPROCKETS', 2, 1, 0, 1.0, 1, False)],
            personal=[('EX_YELLOW', 0, 0, 100, 0.0, 0), ('ROBOT1', 1, 0, 5, 0.0, 2)],
        )
        mod._requirements = {'SHIPSLOT_DMG4': (('LUSH1', 300), ('ROBOT1', 20)), 'SHIPSLOT_DMG9': (('EX_YELLOW', 150),)}
        mod._names._keys = {}
        orig = game.repair_factor
        try:
            game.repair_factor = lambda: 0.5
            board = mod._board(p.ps)
        finally:
            game.repair_factor = orig
        panel = m.core.repair_panel(board, mod._names.name, max_rows=m.screen.ROWS)
        self.assertEqual((panel.title, panel.subtitle), ('Ship Repairs', '3 damaged parts'))
        self.assertEqual(board.unknown, 1)               # SHIPROCKETS: no requirements known here
        self.assertEqual([(r.label, r.value) for r in panel.rows], [
            ('Repair now (F7)', '1'), ('Need materials', '1'), ('Need repair screen', '1'), ('ROBOT1', '5 / 10')])

    def test_grids_from_game_memory(self):
        m = self.load()
        mod = m.NMSTrackerMod()
        p = FakePlayer(
            general=[('SHIPSLOT_DMG9', 4, 4, 3, 1.0, 1, True)],
            tech=[('SHIPROCKETS', 2, 1, -1, 1.0, 1, False)],
            personal=[('COPPER', 0, 0, 20, 0.0, 0)],
            in_progress=[(5, 1, 2, 1, [('R0_SHIPROCKETS', 1.0, False), ('R1_SHIPROCKETS', 0.0, False)])],
        )
        mod._requirements = {'SHIPSLOT_DMG9': (('COPPER', 150),), 'SHIPROCKETS': (('FUEL1', 100), ('LAND1', 50))}
        mod._names._keys, mod._names._kinds = {}, {'COPPER': game.SUBSTANCE, 'FUEL1': game.SUBSTANCE}
        orig = game.repair_factor
        try:
            game.repair_factor = lambda: 0.5
            mod.board = mod._board(p.ps)
            mod._fill_grids(p.ps)
        finally:
            game.repair_factor = orig
        for store in (mod.grids.parts, mod.grids.materials):     # what the grid hook does before each draw
            store.build(m.display.construct_like_game)
            store.apply(10)
        parts = game.elements(game.store_at(mod.grids.parts.address))
        self.assertEqual([(r[0], r[1], r[2], r[4], r[6]) for r in parts],
                         [('SHIPSLOT_DMG9', 0, 0, 1.0, True), ('SHIPROCKETS', 1, 0, 1.0, False)])
        mats = game.store_at(mod.grids.materials.address)
        self.assertEqual([(str(e.Id).strip('\x00'), int(e.Amount), int(e.MaxAmount)) for e in mats.mStore],
                         [('FUEL1', 0, 100), ('COPPER', 20, 75)])     # have / needed, most missing first

    def test_settings_hide_every_pymhf_window(self):
        from pymhf.utils.parse_toml import read_pymhf_settings
        cfg = read_pymhf_settings(str(ROOT / 'mod' / 'nmstracker_mod.py'), True)
        self.assertFalse(cfg['gui']['shown'])
        self.assertFalse(cfg['logging']['shown'])

    def test_text_helpers(self):
        m = self.load()
        self.assertEqual(m.used_text({'CARBON_SEAL': -1, 'FUEL1': 5}), '1 CARBON_SEAL')
        self.assertEqual(m.used_text({}), 'nothing')
        self.assertEqual(m.used_text({'NANOTUBES': -3}, {'NANOTUBES': 'Carbon Nanotubes'}.get), '3 Carbon Nanotubes')
        self.assertEqual((m.plural(1, 'part'), m.plural(3, 'step')), ('1 part', '3 steps'))


if __name__ == '__main__':
    unittest.main()
