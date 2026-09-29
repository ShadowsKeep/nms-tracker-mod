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
NMS Tracker Mod (in development). Everything happens inside the game: no extra windows.

F7 in a damaged part's repair screen: repairs every step of that part you can afford.
F7 anywhere else: repairs every damaged part of your current ship you can afford (damaged
components included). Parts that still need installing, or whose repair was started step by
step, are left for their repair screen.

All repairs go through the game's own repair function at the game's own cost (full cost for a
part being installed, the discounted repair cost for an installed part that got damaged). The
result shows as a normal on-screen game message. Saves are backed up before the first repair
of each session. Logs and reports go to logs\\ and reports\\ next to this file.

Run from cmd or PowerShell (game closed), from this folder:
    pymhf run nmstracker_mod.py

NMS.py is incomplete and game updates can break mods that use it; broken saves are the user's
responsibility (its own warning). That is why every session backs up all save slots first, and
why the mod switches itself off on any game build other than the one it was made for.
"""
import json
import logging
import sys
import time
from pathlib import Path

from pymhf import Mod
from pymhf.core.hooking import on_key_pressed
from pymhf.core.memutils import get_addressof
from pymhf.gui.decorators import no_gui

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mod import backup, core, game, tab  # noqa: E402

from nmspy.common import gameData  # noqa: E402
from nmspy.decorators import main_loop  # noqa: E402

logger = logging.getLogger('NMSTracker')
REPAIR_KEY = 'f7'
REPORTS = HERE / 'reports'
TAG = 'NMS Tracker: '


def used_text(materials: dict, name=lambda gid: gid) -> str:
    used = [f'{-n} {name(gid)}' for gid, n in sorted(materials.items()) if n < 0]
    return ', '.join(used) if used else 'nothing'


def plural(n: int, word: str) -> str:
    return f'{n} {word}' + ('' if n == 1 else 's')


@no_gui
class NMSTrackerMod(Mod):
    __author__ = 'Shadowskeep LLC'
    __description__ = 'REPAIRS inventory tab; F7 repairs the open repair screen, or your whole ship'
    __version__ = '0.4.0'

    def __init__(self):
        super().__init__()
        self._pending = False
        self._busy = False              # True while our own calls run, so we only check the game's
        self._backed_up = False
        self._build_checked = False
        self._enabled = False
        self._rule_broken = False       # set if the game ever uses a cost flag our rule didn't predict
        self._names = game.Names()      # item names in the player's language, loaded on first use
        self._requirements = None       # tech id -> repair requirements, from the game's own table
        self.tab = tab.RepairsTab(logger, self._tab_changed)
        self.tab.set_enabled(False)     # switched on once the game build is known to be supported

    # Every time the game's own menu asks "can this be afforded?", check that our cost rule
    # (full cost = not FullyInstalled) gives the same flag. If it ever differs, F7 stops.
    @game.GameRepair.CanRepairTechnology.after
    def check_cost_rule(self, this, lpStore, liGroup, lIndex, lbFullCost, _result_):
        if self._busy or self._rule_broken or not lpStore:
            return
        try:
            x, y = int(lIndex.contents.X), int(lIndex.contents.Y)
            item = next((e for e in game.elements(game.store_at(get_addressof(lpStore))) if (e[1], e[2]) == (x, y)), None)
            if item is not None and game.full_cost(item) != bool(lbFullCost):
                self._rule_broken = True
                logger.error(f'Cost rule mismatch on {item[0]} (group {int(liGroup)}): game used full cost = '
                             f'{bool(lbFullCost)}, rule says {game.full_cost(item)}. F7 is off for this session.')
        except Exception:
            logger.exception('Could not check the cost setting the game used')

    # ── the REPAIRS tab: thin hooks, all logic in mod/tab.py ─────────────────
    @tab.GameTabs.PrevNextPage.before
    def _tab_prev_next_before(self, this, lbNext):
        self.tab.before_prev_next(this, lbNext)

    @tab.GameTabs.PrevNextPage.after
    def _tab_prev_next_after(self, this, lbNext):
        self.tab.after_prev_next(this, lbNext)

    @tab.GameTabs.OpenPage.before
    def _tab_open_before(self, this, liPage, lbFlag):
        return self.tab.before_open(this, liPage, lbFlag)     # a tuple here swaps the page the game opens

    @tab.GameTabs.OpenPage.after
    def _tab_open_after(self, this, liPage, lbFlag, _result_):
        self.tab.after_open(this, liPage, lbFlag, _result_)

    @tab.GameTabs.DrawPageSelectBar.before
    def _tab_draw_before(self, this, lpBarTop, lpTabList, lbShow):
        self.tab.before_draw(this, lpBarTop, lpTabList, lbShow)

    @tab.GameTabs.DrawPageSelectBar.after
    def _tab_draw_after(self, this, lpBarTop, lpTabList, lbShow):
        self.tab.after_draw(this, lpBarTop, lpTabList, lbShow)

    def _tab_changed(self, active: bool, why: str):
        """REPAIRS selected: show the ship's repair summary (runs on the game thread)."""
        if not active:
            return
        try:
            ps = gameData.player_state
            if ps is None:
                return
            self._load_game_data()
            self._say(self._summary_text(ps))
        except Exception:
            logger.exception('Could not show the repair summary')

    def _summary_text(self, ps) -> str:
        targets, needs_screen = game.ship_repair_targets(ps)
        plan = game.ship_plan(ps, self._requirements or {}, game.repair_factor())
        panel = core.repair_panel(plan, len(needs_screen), self._names.name)
        if panel.subtitle == 'No damage':
            return 'Ship Repairs - no damage, nothing to repair'
        now, waiting, screen = (int(r.value) for r in panel.rows[:3])
        parts = [panel.subtitle, f'{now} can be repaired now (F7)']
        if waiting:
            parts.append(f'{waiting} ' + ('needs' if waiting == 1 else 'need') + ' materials')
        if screen:
            parts.append(f'{screen} ' + ('needs its' if screen == 1 else 'need their') + ' repair screen')
        short = game.shortfall_text(plan, self._names.name, limit=2)
        return 'Ship Repairs - ' + ', '.join(parts) + (f' - short of {short}' if short else '')

    @on_key_pressed(REPAIR_KEY)
    def repair_key(self):
        self._pending = True            # runs on the next game frame, on the game's own thread

    @main_loop.after
    def on_frame(self):
        if not self._build_checked:
            self._build_checked = True
            build = game.running_build()
            self._enabled = build == str(game.GAME_BUILD)
            if self._enabled:
                logger.info(f'Game build {build}: supported. F7 repairs; REPAIRS tab on.')
            else:
                logger.error(f'Game build {build or "unknown"} is not {game.GAME_BUILD}; the mod stays off '
                             'until it is updated for this build.')
            self.tab.set_enabled(self._enabled)
        if not self._pending:
            return
        self._pending = False
        if not self._enabled:
            logger.warning('F7 ignored: this game build is not supported.')
            return
        if self._rule_broken:
            self._say('F7 is switched off for this session (the game used an unexpected cost; see the log)')
            return
        try:
            ps = gameData.player_state
            if ps is None:
                return
            self._load_game_data()
            steps = game.open_repair_steps(ps)
            if steps is not None:
                self._repair_open_part(ps, steps)
            else:
                self._repair_ship(ps)
        except Exception:
            logger.exception('Repair run failed')
            self._say('something went wrong, nothing more was repaired (see the log)')
        finally:
            self._busy = False

    # ── helpers ──────────────────────────────────────────────────────────────
    def _load_game_data(self):
        if self._names._keys is not None:
            return
        reality = gameData.GcApplication.mpData.contents.mRealityManager
        try:
            found = self._names.load(reality)
            logger.info(f'Loaded {found} item names from the game')
        except Exception:
            self._names._keys = {}
            logger.exception('Could not load item names; messages will use item ids')
        try:
            self._requirements = game.tech_requirements(reality)
            logger.info(f'Loaded repair costs for {len(self._requirements)} technologies; '
                        f'repair factor {game.repair_factor()}')
        except Exception:
            self._requirements = {}
            logger.exception('Could not load repair costs')

    def _shortfall(self, ps, steps=None) -> str:
        """What is missing to repair every part in place (or every step of the open part), in
        game names ('' if unknown)."""
        try:
            if steps is not None:
                plan = game.steps_plan(ps, steps, self._requirements or {}, game.repair_factor())
            else:
                plan = game.ship_plan(ps, self._requirements or {}, game.repair_factor())
            return game.shortfall_text(plan, self._names.name)
        except Exception:
            logger.exception('Could not work out the shortfall')
            return ''

    def _say(self, text: str):
        logger.info(text)
        try:
            if not game.show_message(TAG + text):
                logger.warning('Could not show the message in game')
        except Exception:
            logger.exception('Could not show the message in game')

    def _backup_once(self) -> bool:
        if self._backed_up:
            return True
        made = backup.backup_all()
        if not made:
            self._say('could not back up your saves, so nothing was repaired')
            return False
        self._backed_up = True
        logger.info(f'Backed up {len(made)} save slot(s) to {made[0].parent}')
        return True

    def _run(self, ps, action, kind: str, extra: dict) -> dict:
        ship_before = game.ship_damage(ps)
        self._busy = True
        try:
            result = action()
        finally:
            self._busy = False
        report = {
            'made': time.strftime('%Y-%m-%d %H:%M:%S'),
            'game_build': game.GAME_BUILD,
            'kind': kind,
            **extra,
            **result,
            'ship_damage_before': ship_before,
            'ship_damage_after': game.ship_damage(ps),
        }
        REPORTS.mkdir(exist_ok=True)
        path = REPORTS / f"repair-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        logger.info(f'Report: {path}')
        return result

    # ── F7 in a repair screen ────────────────────────────────────────────────
    def _repair_open_part(self, ps, steps):
        part = sorted(gid for gid, *_ in game.elements(steps))
        if not game.damaged_tech(steps):
            self._say('nothing left to repair on this part')
            return
        if not self._backup_once():
            return
        calls = game.GameCalls(ps)
        result = self._run(ps, lambda: game.repair_open_part(ps, calls, steps, lambda: game.materials(ps)),
                           'repair screen', {'part_steps': part})
        done, left = len(result['repaired']), len(game.damaged_tech(steps))
        short = self._shortfall(ps, steps) if left else ''
        if done:
            tail = ' - part repaired' if not left else f' - {plural(left, "step")} still need ' + (short or 'materials')
            self._say(f'repaired {plural(done, "step")}, used {used_text(result["materials"], self._names.name)}{tail}')
        elif result['stopped'] == 'nothing affordable':
            self._say('you don\'t have the materials for the next step yet' + (f' - short of {short}' if short else ''))
        else:
            self._say(f'nothing repaired ({result["stopped"]})')

    # ── F7 anywhere else: the whole current ship ─────────────────────────────
    def _repair_ship(self, ps):
        targets, needs_screen = game.ship_repair_targets(ps)
        screen_note = f' - {plural(len(needs_screen), "part")} need their repair screen' if needs_screen else ''
        if not targets:
            self._say(('nothing on your ship can be repaired here' + screen_note) if needs_screen
                      else 'your ship has nothing to repair')
            return
        if not self._backup_once():
            return
        calls = game.GameCalls(ps)
        result = self._run(ps, lambda: game.repair_ship(ps, calls, lambda: game.materials(ps)),
                           'ship', {'targets': targets, 'needs_screen': needs_screen})
        done = len(result['repaired'])
        left = len(game.ship_repair_targets(ps)[0])
        if done:
            short = self._shortfall(ps) if left else ''
            tail = (f' - {plural(left, "part")} still need ' + (short or 'materials')) if left else ''
            self._say(f'repaired {plural(done, "part")}, used {used_text(result["materials"], self._names.name)}{tail}{screen_note}')
        elif result['stopped'] == 'nothing affordable':
            short = self._shortfall(ps)
            self._say('not enough materials for any repair on your ship' + (f' - short of {short}' if short else '')
                      + screen_note)
        else:
            self._say(f'nothing repaired ({result["stopped"]})')
