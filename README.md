# NMS Tracker Mod

[![tests](https://github.com/ShadowsKeep/nms-tracker-mod/actions/workflows/tests.yml/badge.svg)](https://github.com/ShadowsKeep/nms-tracker-mod/actions/workflows/tests.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

An in-game No Man's Sky mod (built on [NMS.py](https://github.com/monkeyman192/NMS.py) /
[pyMHF](https://github.com/monkeyman192/pyMHF)): a **REPAIRS** tab in the inventory, styled
like the game's own tabs, that shows the current ship's damaged parts and repairs them at the
normal material cost.

> **Early release (v0.7.0) for game build 180383 with NMS.py 180383.0.**
> **Download: [NMSTrackerMod.zip](https://github.com/ShadowsKeep/nms-tracker-mod/releases/latest/download/NMSTrackerMod.zip)**
> ([release notes](https://github.com/ShadowsKeep/nms-tracker-mod/releases/latest)). Game functions
> are found by byte pattern; the few data offsets NMS.py doesn't give can move with each game
> update, so the mod checks the build and switches itself off on any other.

## Status (2026-10-04)

| Piece | State |
|---|---|
| Repairs through the game's own `RepairTechnology` (pays, persists in the save) | Working, verified by save diffs |
| **F7 in a repair screen**: repair every step of that part you can afford | Working in game (save diffs: parts installed, charged, repair records cleared) |
| **F7 anywhere else**: repair every affordable damaged part of the ship | Runs in game; so far only on ships where nothing was affordable in place, so a real whole-ship repair is still to be seen |
| Messages through the game's own on-screen notifications, item names in the game's language | Working in game |
| **REPAIRS tab** in the inventory tab row (click and Q/E / A/D select it) | Working in game (spikes `s3b` / `s3c`, mod v0.4) |
| **REPAIRS screen**: the page's own right-hand panel shows the repair summary | Working in game (v0.5.1) |
| **REPAIRS grids**: the Starship grids show the damaged parts ("Repair") and the materials they need as have / needed ("Materials"), locked | Working in game (build 180383, 2026-10-03): 11 damaged parts with the game's damage markers; materials read "8 / 420" in the slot. Known: a material you have none of, and products (e.g. Wiring Loom), show no count in the slot (the right-hand panel and the log still give have / needed) |
| **Build 180383 + NMS.py 180383.0** (v0.7.0) | Tab, screen, grids, A/D, Starship click, Esc and F7 ran in game 2026-10-03, no errors |

No extra windows: pyMHF's GUI and log console are turned off; everything happens in the game.

## Install (players)

Download [NMSTrackerMod.zip](https://github.com/ShadowsKeep/nms-tracker-mod/releases/latest/download/NMSTrackerMod.zip)
and unzip it anywhere: it is one folder, `NMSTrackerMod`, with a player guide inside
(`README.txt`). With the game closed, from cmd or PowerShell:

```
python -m pip install nmspy==180383.0
pymhf run "C:\path\to\NMSTrackerMod\nmstracker_mod.py"
```

That runs the mod on its own, with no extra windows. To run it with your other NMS.py mods instead,
copy the `NMSTrackerMod` folder into your NMS.py mod directory (set it once with
`pymhf config nmspy`) and start with `pymhf run nmspy`. NMS.py loads only `nmstracker_mod.py`;
the mod's own code sits one level down in `nmstracker\`, so it's never mistaken for a mod.

## Try it from the repo (cmd or PowerShell, game closed)

```
python -m pip install -r requirements.txt
cd nms-tracker-mod\NMSTrackerMod
pymhf run nmstracker_mod.py
```

Build the download zip with `python tools\package.py` (it writes `dist\NMSTrackerMod.zip`).

- **REPAIRS tab** (inventory, next to the last tab): the ship's damage in the game's own panel,
  with bars for parts you can repair now, parts that need materials, parts that need their
  repair screen, and the materials you are short of as "have / needed" for all the damage
  (parts with a repair already started count only their unpaid steps).
- **F7 in a damaged part's repair screen:** repairs every step of that part you can afford.
- **F7 anywhere else:** repairs every damaged part of your current ship you can afford,
  damaged components included. Parts that still need installing, or whose repair was started
  step by step, are left for their repair screen (the message says how many).

Costs are the game's own: `ceil(amount * DamageRepairFactor)` for an installed part that got
damaged, full cost for a part being installed (the game's rule: full cost when the element is
not `FullyInstalled`). Before the first repair of a session the mod copies your save slots to
`%LOCALAPPDATA%\ShadowskeepLLC\NMSTrackerMod\backups\` (newest 5 per slot are kept).

The tab spikes run the same way: `cd nms-tracker-mod\spikes` then `pymhf run s3c_tab_select.py`.

## How the tab works

No data mod: the tab row (`PAGESELECTBAR`, slots `OPTION1..7`) is drawn every frame by the
game's `cGcFrontendPageFunctions::DoToolbar`; the inventory never uses more than 5 slots, so the mod fills the next
free one after the game draws the row. The tab is "Starship page + a mod flag", so the game never
sees an unknown page. Clicks use the game's own click test; Q/E (A/D) are handled by adjusting the
page the game's own previous/next code opens.

The screen itself: the game refills the Starship page's right-hand panel every frame before it
draws the tab row, so right after the tab row the mod rewrites the panel through the game's own
helpers (`SetPageTitle`, `SetStatRow`) and draws its own copies in the grids. It only changes things the
game rewrites itself every frame, so when REPAIRS is left the game's next frame is simply normal
again; nothing has to be put back. Details, addresses and the reasoning are in
[`spikes/README.md`](spikes/README.md).

## Warning (carried over from NMS.py)

NMS.py is missing a lot of game info that gets added over time, and game updates can easily
break mods that use it. Responsibility for broken saves is on the user. Test on a spare save
first, and keep the backups.

## Layout

```
NMSTrackerMod/                      the mod folder (this is what goes into NMS.py's mod folder)
  nmstracker_mod.py                 the mod: REPAIRS tab hooks, F7, cost-rule check, backups, reports, messages
  nmstracker/                       its package (one level down, so pyMHF never loads these files as mods)
    tab.py                          the REPAIRS tab (drawing, click and Q/E selection), from spike S3c
    screen.py                       the REPAIRS screen (summary in the page's own right-hand panel)
    display.py                      display-only inventory copies for the REPAIRS grids
    grids.py                        draws those copies in the game's own Starship grids, locked
    nms_ext.py                      game functions NMS.py doesn't declare yet, written the NMS.py way
    game.py                         memory readers, repair loop, costs, names, messages
    core.py                         repair planning and the screen's summary, pure Python
    backup.py                       save-slot backup before repairs (read-only on the game's files)
spikes/                             step-by-step experiments (S1 read, S2 repair, S3 UI / tab), build 179666
tests/                              python -m unittest discover -s tests (offline; fake game memory in ctypes)
test-logs/                          logs and repair reports from the in-game test runs (user paths replaced)
```

Not in the repo: `work/` (static analysis notes and disassembly of the game executable),
save-data reports and UI dumps from the spikes.

## Game build and NMS.py

The mod was built and tested on game build 179666 with NMS.py 180132.0, which was made for build
180132, so some offsets differed between the two (build differences, not NMS.py mistakes). The
game has since updated to **build 180383**, and **NMS.py 180383.0** is made for exactly that
build, so from v0.7 the mod uses NMS.py's own declarations wherever they exist:
`cGcFrontendManager.Activate` (opening a page), `cGcFrontendPageFunctions.DoToolbar` (the tab
row), `cGcPlayerNotifications.AddTimedMessage`, `cTkLanguageManager.GetInstance`,
`cTkLanguageManagerBase.Translate_internal`, the `cGcInventoryStore` constructor, the
`cGcNGuiLayer` element finders, and NMS.py's structures (player state, inventory store and
elements, repair records, UI element data, player globals, `gameData`).

Game functions the mod needs that NMS.py 180383.0 does not declare are in
[`NMSTrackerMod/nmstracker/nms_ext.py`](NMSTrackerMod/nmstracker/nms_ext.py), written the NMS.py way so they can be offered upstream. The
names are descriptive; the game's own names aren't known here:

| Declared as | RVA (180383) | What it does |
|---|---|---|
| `cGcPlayerState.RepairTechnology` | 0x5963A0 | pays and repairs one damaged slot or repair step |
| `cGcPlayerState.CanRepairTechnology` | 0x595FD0 | the can-afford check the repair menu uses |
| `cGcPlayerInventories.GetInventory` | 0x47DDA0 | inventory group -> store (called on `cGcPlayerState + 0x900`) |
| `cGcFrontendManager.PrevNextPage` | 0x8FAD60 | previous / next inventory tab (A/D, Q/E) |
| `cGcFrontendManager.RequestPage` | 0x314B40 | ask for a page, like a tab click |
| `cGcFrontendPageFunctions.SetPageTitle` | 0x7CD430 | TITLE > MAINTEXT / SUBTEXT |
| `cGcFrontendPageFunctions.DoInventory` | 0x6B1240 | one inventory grid; calls `DoInventorySlots` |
| `cGcFrontendPageFunctions.SetStatRow` | 0x6ADEE0 | one stat row (name, value, bar) in the right-hand panel |

More in [`spikes/README.md`](spikes/README.md).

## Contributing

Bug reports, testing on other saves and game builds, and code are welcome: see
[CONTRIBUTING.md](CONTRIBUTING.md) (setup, tests, the mod's safety rules, updating for a new game
build) and the [code of conduct](CODE_OF_CONDUCT.md). Questions and ideas go to
[Discussions](https://github.com/ShadowsKeep/nms-tracker-mod/discussions).

## Licence

[MIT](LICENSE), the same as NMS.py and pyMHF, so the game function declarations in
`NMSTrackerMod/nmstracker/nms_ext.py` can move into NMS.py freely. No Man's Sky and its data belong to Hello Games; this
repo contains no game files.

## Requirements (for players, once released)

- No Man's Sky on PC (Steam; GOG to be confirmed) on a build the mod and NMS.py support
- Python from python.org (not the Microsoft Store build)
- `python -m pip install -r requirements.txt` (NMS.py for the game build the mod supports)

Free companion app: [NMS Tracker](https://github.com/ShadowsKeep/nms-tracker/releases/latest).
Support: https://buymeacoffee.com/fxshadowking
