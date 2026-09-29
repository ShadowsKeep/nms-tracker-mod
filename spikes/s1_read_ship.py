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
Spike S1: read the current ship. READ-ONLY.

This script only reads game memory. It calls no game functions and writes nothing into the
game; its only output is a JSON report and log lines in this folder (logs\\ and s1-*.json).

Run (with the game closed, from this folder):
    pymhf run s1_read_ship.py

Then load the SPARE save, get to where you can open your inventory, and press
"Read ship" in the NMS.py window. Hover each damaged slot in the game and compare the cost
the game shows with the report.
"""
import json
import logging
import time
from pathlib import Path

from pymhf import Mod
from pymhf.core.memutils import get_addressof, map_struct
from pymhf.gui.decorators import gui_button

import nmspy.data.types as nms
from nmspy.common import gameData
from nmspy.data.enums import InventoryChoice
from nmspy.decorators import main_loop

logger = logging.getLogger('S1')
OUT = Path(__file__).resolve().parent

# Build 179666's code uses 0xAB28 for the ship tech inventories (static scan, work/re), right after
# the cargo array (0x8FC8 + 12 * 0x248). NMS.py 180132.0 has 0xAA28, for build 180132: a build
# difference, so read it from 179666 itself.
SHIP_TECH_OFFSET = 0xAB28
STORE_SIZE = 0x248
SPECIAL_DAMAGE = {'Broken', 'BlockedByBrokenTech'}

MATERIAL_SOURCES = {       # label -> InventoryChoice; the ship's own stores are read by ship index below
    'exosuit': InventoryChoice.Suit,
    'exosuit_cargo': InventoryChoice.Suit_Cargo,
    'freighter': InventoryChoice.Freighter,
    'freighter_cargo': InventoryChoice.Freighter_Cargo,
    'current_ship_via_choice': InventoryChoice.Ship,
    'current_ship_cargo_via_choice': InventoryChoice.Ship_Cargo,
}


def _enum_name(value):
    return getattr(value, 'name', None) or str(value)


def _plausible(store):
    """Sanity check before following any pointer, so a wrong offset is skipped, not a crash."""
    try:
        w, h, cap = int(store.miWidth), int(store.miHeight), int(store.miCapacity)
        n = len(store.mStore)
    except Exception:
        return False
    return 0 <= w <= 16 and 0 <= h <= 16 and 0 <= cap <= 256 and 0 <= n <= max(cap, w * h, 1)


def _ship_tech_store(ps, index):
    return map_struct(get_addressof(ps) + SHIP_TECH_OFFSET + index * STORE_SIZE, nms.cGcInventoryStore)


def _special(store):
    if not _plausible(store) or len(store.maSpecialSlots) > 256:
        return []
    return [{'x': int(s.Index.X), 'y': int(s.Index.Y), 'type': _enum_name(s.Type)} for s in store.maSpecialSlots]


def _slots(store):
    """Every filled slot of one inventory store, as plain data."""
    if not _plausible(store):
        logger.warning('Skipped an inventory that does not look valid (offset may be wrong for this build).')
        return []
    out = []
    for el in store.mStore:
        gid = str(el.Id).strip('\x00 ')
        if not gid:
            continue
        out.append({
            'id': gid,
            'x': int(el.Index.X),
            'y': int(el.Index.Y),
            'amount': int(el.Amount),
            'max': int(el.MaxAmount),
            'damage': round(float(el.DamageFactor), 4),
            'type': _enum_name(el.Type),
            'installed': bool(el.FullyInstalled),
        })
    return out


def _totals(slots):
    out = {}
    for s in slots:
        if s['type'] != 'Technology' and s['amount'] > 0:
            key = s['id'].lstrip('^')
            out[key] = out.get(key, 0) + s['amount']
    return out


def _tech_table():
    """Game technology table as {id: tech}, taken from the running game (so it matches this build)."""
    reality = gameData.GcApplication.mpData.contents.mRealityManager
    table = {}
    for tech in reality.mpTechnologyTable.contents.Table:
        table[str(tech.ID).strip('\x00 ')] = tech
    repair_techs = []
    for ptr in reality.mapRepairTechs:
        try:
            repair_techs.append(str(ptr.contents.ID).strip('\x00 '))
        except (ValueError, AttributeError):
            repair_techs.append('?')
    return table, repair_techs


def _tech_info(tech):
    if tech is None:
        return None
    return {
        'name': str(tech.Name).strip('\x00 '),
        'repair_tech': bool(tech.RepairTech),
        'procedural': bool(tech.Procedural),
        'category': _enum_name(tech.Category),
        'damaged_description': str(tech.DamagedDescription)[:200],
        'requirements': [
            {'id': str(r.ID).strip('\x00 '), 'amount': int(r.Amount), 'type': _enum_name(r.Type)}
            for r in tech.Requirements
        ],
    }


class S1ReadShip(Mod):
    __author__ = 'Shadowskeep LLC'
    __description__ = 'Spike S1: read the current ship and repair costs (read-only)'
    __version__ = '0.1'

    def __init__(self):
        super().__init__()
        self._pending = False

    @gui_button('Read ship')
    def request_read(self):
        # Reading happens on the game's own thread (main_loop) so no inventory changes under us.
        self._pending = True
        logger.info('Read requested; it will run on the next game frame.')

    @main_loop.after
    def on_frame(self):
        if not self._pending:
            return
        self._pending = False
        try:
            self._read()
        except Exception:
            logger.exception('S1 read failed')

    def _read(self):
        ps = gameData.player_state
        if ps is None or gameData.GcApplication is None:
            logger.warning('No player state yet. Load the spare save first.')
            return

        primary = int(ps.miPrimaryShip)
        if not 0 <= primary < 12:
            logger.warning(f'Unexpected primary ship index {primary}; stopping.')
            return
        stores = {
            'general': ps.mShipInventories[primary],
            'cargo': ps.mShipInventoriesCargo[primary],
            'tech': _ship_tech_store(ps, primary),
        }
        ship_general = _slots(stores['general'])
        ship_cargo = _slots(stores['cargo'])
        ship_tech = _slots(stores['tech'])
        special = {k: _special(v) for k, v in stores.items()}

        table, repair_techs = _tech_table()
        damaged = []
        for where, slots in (('general', ship_general), ('cargo', ship_cargo), ('tech', ship_tech)):
            for s in slots:
                if s['damage'] > 0 and s['type'] == 'Technology':   # junk items can carry damage too
                    gid = s['id'].lstrip('^')
                    damaged.append({**s, 'inventory': where, 'tech': _tech_info(table.get(gid))})

        materials = {
            'current_ship': _totals(ship_general),
            'current_ship_cargo': _totals(ship_cargo),
        }
        for label, choice in MATERIAL_SOURCES.items():
            materials[label] = _totals(_slots(ps.mInventories[int(choice)]))

        report = {
            'made': time.strftime('%Y-%m-%d %H:%M:%S'),
            'primary_ship_index': primary,
            'ship_name': str(ps.maCustomShipNames[primary]).strip('\x00 '),
            'damaged_slots': damaged,
            'special_slots': special,          # Broken = units repair; BlockedByBrokenTech = material repair
            'repair_techs_listed_by_game': repair_techs,
            'materials': materials,
            'ship_slot_counts': {'general': len(ship_general), 'cargo': len(ship_cargo), 'tech': len(ship_tech)},
            'ship_tech_ids': sorted({s['id'] for s in ship_tech}),
        }
        path = OUT / f"s1-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')

        broken = sum(1 for slots in special.values() for s in slots if s['type'] in SPECIAL_DAMAGE)
        logger.info(f'Ship {report["ship_name"] or primary}: {len(damaged)} damaged slots, '
                    f'{broken} damage markers. Report: {path}')
        for d in damaged:
            cost = ', '.join(f"{r['amount']} {r['id']}" for r in (d['tech'] or {}).get('requirements', [])) or 'unknown'
            logger.info(f"  [{d['inventory']} {d['x']},{d['y']}] {d['id']} damage={d['damage']} cost: {cost}")
