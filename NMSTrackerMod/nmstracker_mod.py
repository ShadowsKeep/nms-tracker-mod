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
sys.path.insert(0, str(HERE))         # nmstracker\ sits next to this file
from nmstracker import backup, core, display, game, grids, nms_ext, screen, tab  # noqa: E402

import nmspy.data.types as nms  # noqa: E402
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


def with_this(this, args):
    """Replacement arguments from the logic (which works on addresses) with the game's own
    `this` put back in front, as the hook received it; None stays None (nothing replaced)."""
    return None if args is None else (this,) + tuple(args[1:])


@no_gui
class NMSTrackerMod(Mod):
    __author__ = 'Shadowskeep LLC'
    __description__ = 'REPAIRS inventory tab; F7 repairs the open repair screen, or your whole ship'
    __version__ = '0.7.0'

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
        self.screen = screen.RepairsScreen(logger)
        self._screen_stale = True       # recompute the REPAIRS screen content on the next frame
        self._screen_time = 0.0
        self._screen_failed = False
        self.board = core.Board([], [])     # damaged parts + materials, refreshed with the screen
        self._board_logged = None
        self.grids = grids.RepairGrids(logger)   # the REPAIRS grids: display-only copies, locked
        self._grids_ok = None                    # None until REPAIRS first opens (saves backed up first)

    # Every time the game's own menu asks "can this be afforded?", check that our cost rule
    # (full cost = not FullyInstalled) gives the same flag. If it ever differs, F7 stops.
    @nms_ext.cGcPlayerState.CanRepairTechnology.after
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

    # ── the REPAIRS tab: thin hooks, all logic in nmstracker/tab.py ─────────────────
    # `this` arrives as a pointer to its class (NMS.py style); the logic works on addresses.
    @nms_ext.cGcFrontendManager.PrevNextPage.before
    def _tab_prev_next_before(self, this, lbNext):
        self.tab.before_prev_next(get_addressof(this), lbNext)

    @nms_ext.cGcFrontendManager.PrevNextPage.after
    def _tab_prev_next_after(self, this, lbNext):
        self.tab.after_prev_next(get_addressof(this), lbNext)

    @nms.cGcFrontendManager.Activate.before
    def _tab_open_before(self, this, lePage, lbTransitionRight):
        # a tuple here swaps the page the game opens; the game gets its own `this` back
        return with_this(this, self.tab.before_open(get_addressof(this), lePage, lbTransitionRight))

    @nms.cGcFrontendManager.Activate.after
    def _tab_open_after(self, this, lePage, lbTransitionRight, _result_):
        self.tab.after_open(get_addressof(this), lePage, lbTransitionRight, _result_)
        self.screen.forget()                    # a page open reloads the layout: old elements are gone

    # ── the REPAIRS grids: all logic in nmstracker/grids.py ─────────────────────────
    @nms_ext.cGcFrontendPageFunctions.DoInventory.before
    def _grid_before(self, lpPage, lpInventory, lpInventoryGuiLayer, lbAccessible, lEmptySlotActions, lbViewOnly,
                     lpbOut, liPopupActions, liMinRows, liSlotsWide, liSlotSize, lbNoScroll):
        # a tuple here draws a display store, locked, instead of the game's own grid
        args = self.grids.before_do_inventory(
            get_addressof(lpPage), get_addressof(lpInventory), get_addressof(lpInventoryGuiLayer), lbAccessible,
            lEmptySlotActions, lbViewOnly, lpbOut, liPopupActions, liMinRows, liSlotsWide, liSlotSize, lbNoScroll)
        if args is None:
            return None
        return (lpPage, grids.store_pointer(args[1]), lpInventoryGuiLayer) + tuple(args[3:])

    # the tab row: NMS.py's cGcFrontendPageFunctions.DoToolbar (static, page first)
    @nms.cGcFrontendPageFunctions.DoToolbar.before
    def _tab_draw_before(self, lpPage, lpParentLayer, lpPageGroup, lbActive):
        self.tab.before_draw(get_addressof(lpPage), get_addressof(lpParentLayer), int(lpPageGroup or 0), lbActive)

    @nms.cGcFrontendPageFunctions.DoToolbar.after
    def _tab_draw_after(self, lpPage, lpParentLayer, lpPageGroup, lbActive):
        this = get_addressof(lpPage)
        lpBarTop, lpTabList, lbShow = get_addressof(lpParentLayer), int(lpPageGroup or 0), lbActive
        self.tab.after_draw(this, lpBarTop, lpTabList, lbShow)
        try:
            if self.tab.showing:
                if self._grids_ok is None:
                    self._grids_ok = self._backup_before_grids()
                self._refresh_screen()
                self.screen.draw(this, self._grid_mode())   # after the game filled the page: ours shows this frame
            else:
                self.screen.forget()               # nothing to put back: the game redraws its own panel
        except Exception:
            if not self._screen_failed:             # once: this runs every frame
                self._screen_failed = True
                logger.exception('REPAIRS screen failed')
        finally:
            # the grids are drawn earlier in the NEXT frame (DoInventory runs inside the page body)
            self.grids.active = bool(self.tab.showing and self._grids_ok)
            self.grids.frame_done()
        return None

    def _grid_mode(self) -> str:
        if self.grids.drawn:
            return screen.GRIDS_MOD
        if self.grids.held:
            return screen.GRIDS_REAL                # holding an item: the real grids stay usable
        return screen.GRIDS_HIDDEN

    def _backup_before_grids(self) -> bool:
        """The grids are drawn by the game from mod memory: back the saves up first, once a session."""
        if self._backed_up:
            return True
        try:
            made = backup.backup_all()
        except Exception:
            logger.exception('Backing up the saves failed')
            made = []
        if not made:
            logger.warning('Could not back up your saves: the REPAIRS grids stay off this session')
            return False
        self._backed_up = True
        logger.info(f'Backed up {len(made)} save slot(s) to {made[0].parent} before showing the REPAIRS grids')
        return True

    def _refresh_screen(self):
        """Recompute the REPAIRS screen at most twice a second (and right after F7 / selecting)."""
        now = time.monotonic()
        if not self._screen_stale and now - self._screen_time < 0.5:
            return
        ps = gameData.player_state
        if ps is None:
            return
        self._load_game_data()
        self.board = self._board(ps)
        self.screen.set_content(core.repair_panel(self.board, self._names.name, max_rows=screen.ROWS))
        self._fill_grids(ps)
        self._screen_stale, self._screen_time = False, now
        text = core.board_text(self.board, self._names.name)
        if text != self._board_logged:              # only when what the screen shows changes
            self._board_logged = text
            logger.info(f'REPAIRS screen: {text}')

    def _board(self, ps) -> core.Board:
        return game.repair_board(ps, self._requirements or {}, game.repair_factor())

    def _fill_grids(self, ps):
        """Damaged parts (top grid) and the materials they need (bottom grid), as display copies.
        The grid hook writes them into the stores right before each draw."""
        self.grids.parts.set_items(display.part_items(self.board.parts, game.part_rows(ps)))
        self.grids.materials.set_items(display.material_items(self.board.materials, self._names.kind))
        if self.grids.parts.ready:
            self.grids.parts.set_style(*game.store_style(ps, 5))
            self.grids.materials.set_style(*game.store_style(ps, 6))

    def _tab_changed(self, active: bool, why: str):
        """REPAIRS selected or left: the screen itself shows the summary now."""
        self._screen_stale = True

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
                try:
                    found = game.load_globals()
                except Exception:
                    logger.exception('Mapping the game globals with NMS.py failed')
                    found = False
                if not found:
                    logger.warning('NMS.py did not find GcPlayerGlobals / GcUIGlobals: costs show as unknown, no messages')
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
            self._screen_stale = True               # the REPAIRS screen shows the new numbers next frame
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
