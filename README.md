# NMS Tracker Mod

An in-game No Man's Sky mod (built on [NMS.py](https://github.com/monkeyman192/NMS.py) /
[pyMHF](https://github.com/monkeyman192/pyMHF)): a **REPAIRS** tab in the inventory, styled
like the game's own tabs, that shows the current ship's damaged parts and repairs them at the
normal material cost.

> **Work in progress, game build 179666 only.** Every game address in here is for that build
> and moves with each game update; the mod checks the build and switches itself off on any
> other. Not released yet.

## Status (2026-09-29)

| Piece | State |
|---|---|
| Repairs through the game's own `RepairTechnology` (pays, persists in the save) | Working, verified by save diffs |
| **F7**: repair every affordable step of the open repair screen, or every affordable damaged part of the ship | Working in game |
| Messages through the game's own on-screen notifications, item names in the game's language | Working in game |
| **REPAIRS tab** in the inventory tab row (click and Q/E / A/D select it) | Working in game as spikes `s3b` / `s3c`; built into the mod (v0.4), not yet run in game there |
| Repair summary when REPAIRS is selected | v0.4: as an in-game message; in the page's own right-hand panel next |

No extra windows: pyMHF's GUI and log console are turned off; everything happens in the game.

## Try it (cmd or PowerShell, game closed)

```
python -m pip install nmspy
cd nms-tracker-mod\mod
pymhf run nmstracker_mod.py
```

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
game's `DrawPageSelectBar`; the inventory never uses more than 5 slots, so the mod fills the next
free one after the game draws the row. The tab is "Starship page + a mod flag", so the game never
sees an unknown page. Clicks use the game's own click test; Q/E (A/D) are handled by adjusting the
page the game's own previous/next code opens. Details, addresses and the reasoning are in
[`spikes/README.md`](spikes/README.md).

## Warning (carried over from NMS.py)

NMS.py is missing a lot of game info that gets added over time, and game updates can easily
break mods that use it. Responsibility for broken saves is on the user. Test on a spare save
first, and keep the backups.

## Layout

```
mod/nmstracker_mod.py  the NMS.py mod: REPAIRS tab hooks, F7, cost-rule check, backups, reports, messages
mod/tab.py             the REPAIRS tab (drawing, click and Q/E selection), from spike S3c
mod/game.py            game function declarations, memory readers, repair loop, costs, names
mod/core.py            repair planning across parts and the tab's summary panel, pure Python
mod/backup.py          save-slot backup before repairs (read-only on the game's files)
spikes/                step-by-step experiments (S1 read, S2 repair, S3 UI / tab) and their notes
tests/                 python -m unittest discover -s tests (offline; fake game memory in ctypes)
test-logs/             logs and repair reports from the in-game test runs (user paths replaced)
```

Not in the repo: `work/` (static analysis notes and disassembly of the game executable),
save-data reports and UI dumps from the spikes.

## Notes for NMS.py (build 179666, NMS.py 180132.0)

- `cGcPlayerState.mShipInventoriesTechOnly` is at `0xAB28`, not `0xAA28`.
- `InventoryChoice.Ship_Cargo` / `Ship_Tech` are swapped (the game's `GetInventory`: 5 = tech,
  6 = cargo).
- `cGcPlayerNotifications.AddTimedMessage` takes 11 arguments in this build.
- The frontend manager is at `Data + 0x849020` (page at manager + `0x2BA0`).

More in [`spikes/README.md`](spikes/README.md).

## Requirements (for players, once released)

- No Man's Sky on PC (Steam; GOG to be confirmed) on a build the mod and NMS.py support
- Python from python.org (not the Microsoft Store build)
- `python -m pip install nmspy`

Free companion app: [NMS Tracker](https://github.com/ShadowsKeep/nms-tracker/releases/latest).
Support: https://buymeacoffee.com/fxshadowking
