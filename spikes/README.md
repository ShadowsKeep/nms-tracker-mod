# Phase 0 spikes

Three small experiments decide whether the mod can be built and what shape the tab takes.
Claude writes the scripts; **the owner runs the game**. The owner is fine with spikes running
on any save (2026-09-29); every repair still backs up all save slots first.

## Before the first spike (owner)

1. `python -m pip install nmspy` (Python 3.13 from python.org is already on this PC; if pip
   refuses, NMS.py's README names the Python version it supports).
2. Check NMS.py supports the installed game build: start one of its example mods and confirm
   the game loads with the pyMHF log window.
3. Make a spare save: start a new game in slot 2 (or later), fly until you have a damaged
   ship (a crashed ship claimed at a crash site is ideal), save, quit.
4. Tell Claude when nmspy is installed. Claude then reads the installed package source
   locally to find the real inventory, repair and UI functions instead of guessing, and
   writes the spike scripts below.

## S1: read the ship

Log the current ship's damaged slots and, for each, the materials **the game itself** says the
repair costs, plus the player's counts of those materials.

Script: `s1_read_ship.py` (read-only: it reads memory, calls no game functions). Run it from
**cmd or PowerShell** (not Git Bash; pyMHF needs a real Windows console), with the game closed:

```
cd nms-tracker-mod\spikes
pymhf run s1_read_ship.py
```

Steam starts the game with NMS.py attached; a log window and the NMS.py window open. Load
the spare save (slot 2), then press **Read ship** in the NMS.py window. It writes
`s1-<time>.json` here and logs each damaged slot with the cost the game's tables give.
Logs go to `spikes\logs\`, never the game folder.

Pass: the log matches what the game shows when you hover each damaged slot, and matches the
tracker's save import (`nms-tracker/app/saveimport.py`) for the same save.

## S2: one repair (go/no-go)

Call the game's own repair action for **one** damaged slot on the spare save.

Script: `s2_repair_one.py`. The game's repair routine was found by a read-only static scan
of NMS.exe build 179666 (`work/re/`): `RepairTechnology` at RVA 0x5930A0 (checks and pays the
cost, clears the damage, removes a damaged-component item). It pays for **damaged tech and
damaged components** (material repairs). Crashed-ship "Broken" slots paid in **units** use a
different path (REPAIR_SLOT popup + RVA 0x4E4630); S2 only watches that one.

```
cd nms-tracker-mod\spikes
pymhf run s2_repair_one.py
```

**How the game repairs damaged tech (found in run 3):** each damaged part has its own
repair screen with several **steps** (e.g. `R0_LAUNCHER`, `R1_LAUNCHER`), each paid
separately. The menu repairs a step through **group 24**, a separate store holding the steps
of the part whose repair screen is open (not the ship's own inventory). When all steps are
done the part itself is repaired.

1. Load a save with a damaged part that has at least **two** repair steps you can afford.
   Slot 2's last save (19:59) still has the starter ship's launch thruster and pulse engine
   damaged.
2. Open that part's repair screen and repair **one step** by hand. The log shows
   `[watch] RepairTechnology(...) -> True`, what the step store now holds and the ship's
   damage, then "Repair next step is now enabled".
3. **Keep that repair screen open**, switch to the NMS.py window and press **Repair next
   step**. It backs up every save slot to
   `%LOCALAPPDATA%\ShadowskeepLLC\NMSTrackerMod\backups\`, asks the game for the same
   store, asks the game's can-afford check about each damaged step, and repairs the first one
   it says you can afford, with the same flags the game used. It writes `s2-<time>.json`
   with the materials taken, the steps before/after and the ship's damage before/after.
4. Back in the game: is that step repaired, and did the right materials go? If it was the
   last step, is the part itself repaired?
5. Save (exit the ship or use a save point), quit to the main menu, reload: still repaired,
   materials still gone?
6. Optional: repair one crashed-ship "Broken" slot with units by hand, so the log records
   that path too.

First run (2026-09-29): the game repaired the starter ship's parts with **group 24**, which
the first version of S2 did not accept, so its button stayed disabled (by design). Version
0.2 accepts any group and resolves it through the game's own lookup (RVA 0x47BDB0).

Second run: v0.2 crashed in its own logging (it used `int()` on a `c_enum32` field; use
`.value`) before it recorded the repair, so the button stayed off. The hooks caught the error
and the game was unaffected. v0.3 records the repair before any extra logging, and the button
asks the game about every damaged part and takes the first one it says is affordable
(before, it only tried the first part). The element reader is checked offline against a fake
store laid out like game memory.

Third run: recording worked and revealed the per-part step stores above. The button's
can-afford call then failed in ctypes ("expected LP_cGcPlayerState instance instead of
pointer to GameRepair"): `this` was typed as cGcPlayerState but the call was made on a
GameRepair-mapped instance. The failed call returned None, which v0.3 wrongly logged as "can
repair = False". v0.4 types `this` as `"_Pointer[GameRepair]"`, treats None as a failed call
and stops, and says so when no repair screen is open. An offline check now rebuilds the exact
ctypes argument conversion for every call; it reproduces the v0.3 error and passes on v0.4.

Pass, all of:
- the slot is repaired in the inventory screen
- exactly the game's cost was taken from your materials
- save, quit to menu, reload: the repair and the material change are still there

If this fails, the mod stops here. We do not rebuild the repair by editing the inventory.

## S3: native-looking tab

**S3a (discovery, no extraction needed):** `s3_ui_discovery.py` watches the game's own
layout loader (cGcNGuiLayer::LoadFromMetadata, mapped by NMS.py and unique in build 179666)
and reads each layout's element tree right after the game builds it: class name (from the
game's RTTI), ID, position, size, hidden flag, text and child layout files. Read-only, no
windows. Checked offline against a fake NGui tree built in memory.

```
cd nms-tracker-mod\spikes
pymhf run s3_ui_discovery.py
```
Load a save, open the inventory, click through its tabs (Exosuit, Starship, Multi-Tool,
Freighter, ...), open a damaged part's repair screen, then press **F8**: an in-game message
confirms and `s3-ui-<time>.json` is written here. From that, Claude designs the tab (EXML
patch cloned from the real tab elements) and the code that fills it.

Fallback if the tree is not enough to write the EXML patch: extract the files it names with
NMS Mod Tool, as below.

**What drives the tab row (static scan, `work/re/notes_inventory_tabs.txt`, key points checked
in the code):** the visible row is `PAGESELECTBAR` (OPTION1..7, each with _OFF and _OFF_S) in
the frontend's "BAR TOP" layer, not PAGESELECTBARSUB (that is the Freighter sub-tab row). Each
frame `DrawPageSelectBar` (RVA 0x6C05A0, only caller 0x90924E: page = manager+0x2BA0, list =
manager+0x5BD60, or +0x5BD70 for the pause menu) finds PAGESELECTBAR, formats "OPTION%d" /
"OPTION%d_OFF" / "OPTION%d_OFF_S", looks each up with GetTextSpecial (0x315FB0), writes IsHidden
at `[elem+0x48]+0x61`, translates the tab's loc key and sets it through the element's vtable
+0x90 (0x1E4CF0, plain char*). The tab list holds at most 5 entries (Exosuit, Starship,
Multi-Tool, Exocraft, Freighter), so slots 6 and 7 are always free. A click calls RequestPage
(0x314B10); A/D go through 0x8F57E0 -> OpenPage 0x8F6D40.

**S3b (first visible step):** `s3b_tab_label.py` draws "REPAIRS" in the next free slot, in the
game's own "not selected" style, after the game draws the row each frame. Only that unused
element's text and hidden flag change. F9 toggles it. Checked offline with fake memory and fake
elements (including the vtable set-text call).

```
pymhf run s3b_tab_label.py
```
Open the inventory: REPAIRS should appear greyed after MULTI-TOOL. Clicking does nothing yet.

S3b result (2026-09-29): REPAIRS drawn in slot 4 in the game's own style, and it highlights on
hover like the game's tabs. Log confirmed page = Data+0x84BBC0 and list = Data+0x8A4D80.

**S3c (selection):** `s3c_tab_select.py`. REPAIRS = the Starship page + a mod flag. Click it
(the game's own RequestPage for the Starship page if another page is open), or A/D past either
end (the page the game's A/D code opens is swapped in OpenPage's before-hook); from REPAIRS, D
goes to the first tab and A to the last. While selected it gets the selected look and Starship
the not-selected look. Any other page opening clears it; a click on Starship while REPAIRS is
shown is consumed (the page is already open). The grid under it is still Starship's. Checked
offline through every transition, including a delayed and a refused page request.

```
pymhf run s3c_tab_select.py
```

S3c run 1 (v0.1): clicking REPAIRS on the Starship page worked (selected look, Starship grey:
screenshot). A/D onto REPAIRS selected it and then dropped it ~4 ms later: OpenPage only starts
a page switch (state `manager+0x1C3F8` = 6, target `manager+0x2B4C`, 0x8F70F7) and the current
page `manager+0x17018` changes frames later (0x8F7860). v0.2 keeps REPAIRS while the game is on
or switching to the Starship page, and ignores repeat clicks mid-switch. The offline check now
switches pages over several frames; with the v0.1 rule it fails at the same point as in game.

**REPAIRS screen (mod v0.5, `mod/screen.py`, no spike):** while REPAIRS is selected, the
Starship page's right-hand panel shows the repair summary and the grids are hidden. Static scan
(`work/re/notes_inventory_panel.txt`): for the Ship page the game fills the whole panel every
frame *before* DrawPageSelectBar and renders later, so the mod writes the panel from its
after-hook on DrawPageSelectBar and it shows in the same frame; the next normal Starship frame
puts the game's own content back by itself.

- Page root `*(page+0x14468)`, page enum `*(i32*)(page+0x14478)` (1 = Starship).
- Title: the game's `SetPageTitle` (RVA 0x7C9350, `(page, main, sub, bool)`).
- Rows: the game's `StatRow` (RVA 0x6A9FD0, `(row, label, value, mainW xmm3, bonusW [rsp+20],
  iconId, compare)`) on `STAT_BOX > LIVE_STATS > BASE_STAT_BAR1..4`; a full bar is 320 units.
- Hidden for the frame: `SECTION1`, the top `CLASS_BOX`, `SQU_INV_TECH`, `SQU_INV_REGULAR`,
  `SQU_TECH_BIG`, `SQU_ITEM_BIG`, `TECHHEADER`, `CARGOHEADER`, `EDITGRP`, `FILTERS`.

v0.5.0 run (2026-09-30, both saves): the tab, title, rows, bars and hidden grids all showed
(screenshot: "Ship Repairs", Repair now 0 / Need materials 1 / Need repair screen 10 / Short:
Activated Copper 75). Three problems:

1. **Leaving REPAIRS on save 1 read a freed element** (`access violation reading
   0x544F8C` in `element_id`, caught by ctypes as OSError, no harm done). "REPAIRS left (page 7
   opened)": every page open reloads the layout (0x8F7860 -> 0x8DDC80); the root pointer stayed
   the same but its elements were freed, and the restore step checked the cached ones.
2. The hint written into `SECTION1 > NATURAL` is a 58-unit wide scrolling field: clipped, looped
   ("...ials, then press F7 Gatl") and ran into row 1.
3. The class badge stayed. The live UI dump (spike S3) has **two** `CLASS_BOX` layers: a top one
   (found from the root, the game un-hides it every frame at 0x90613C; the mod hid that one) and
   `STAT_BOX > BASE_STATS > DATA > SECTION2 > CLASS_BOX`, the visible badge. The game never
   un-hides the second one (0x6AA510 writes no hidden flag), so hiding it would stick.

v0.5.1: the mod changes only what the game rewrites itself on every Starship frame (title, rows
1-4, SECTION1 / top CLASS_BOX / grid hidden flags), so nothing is put back on leave and the restore
step is gone. Never touched: NATURAL's text, `BASE_STAT_BAR5`, the SECTION2 badge (kept: it shows
the ship's class). Cached element addresses live only while REPAIRS stays on screen: dropped on
every OpenPage, on another page, a new root, or a frame gap over 0.25 s; the per-frame ID check
reads through `ctypes.string_at`, which turns an access violation into OSError. SUBTEXT (the
subtitle) did not show in game; not pursued.

v0.5.1 run (2026-09-30, both saves): **pass.** Clean panel (title, the ship's class badge, 4
rows; no scrolling text), no errors; REPAIRS left for page 7 in both runs with nothing logged.
F7 on REPAIRS showed the game's own message ("not enough materials for any repair on your ship -
short of 75 Activated Copper - 10 parts need their repair screen"). The save (read-only) confirms
the split: the one damaged component with no repair started (`SHIPSLOT_DMG9`) is the F7 target;
the other 8 components and the 2 technologies each have a `RepairTechBuffer` entry, i.e. a step
repair already started in their repair screen, so repairing them in place would pay twice.
Components get steps too (`R0_SHIPSL<n>`...), once their repair screen has been opened.

Leaving REPAIRS by clicking Starship or with A/D has not been run with the screen yet (only
page 7, the menu closing).

**REPAIRS grids (mod v0.6.0, `mod/grids.py` + `mod/display.py`):** the Starship page's own tech and
cargo grids draw display-only copies: damaged parts ("Repair") and the materials they still need
("Materials", amount = have, max = needed). Static analysis in `work/re/notes_inventory_grid.txt`
(DoInventory 0x6AD330 is hooked before; locked, empty slot actions, mask 0; stores built by the
game's constructor 0x4CA2A0; real grids while an item is held; sort buttons hidden). The owner
accepted that the game marks drawn items as "seen".

v0.6.0 run (2026-09-30, save slot 1): no errors, no crash. Saves backed up first; both grids swapped
(11 parts in SQU_INV_TECH, 7 materials in SQU_INV_REGULAR, 10 columns); both header labels found;
REPAIRS shown for 27 s, left for page 7. The board logged: Paraffinium 0 / 450, Chromatic Metal
8 / 420, Copper 0 / 200, Pure Ferrite 2 / 125, Magnetised Ferrite 16 / 100, Activated Copper 0 / 75,
Wiring Loom 1 / 3. Not yet seen: a screenshot of the grids (icons, the "have/needed" slot text),
and a check that clicking / dragging / hovering does nothing.

The game's archives (`GAMEDATA\PCBANKS\NMSARC.*.pak`) are in Hello Games' newer **HGPA**
format (magic `HGPA`, checked 2026-09-29), not PSARC, so an up-to-date tool is needed.
Owner: install NMS Mod Tool (Nexus #4312, needs the .NET 10 Desktop Runtime), browse to the
`UI` folder, and extract + decompile everything whose name mentions INVENTORY (plus anything
under UI that looks like the inventory page or its tabs) to MXML in
`nms-tracker-mod\work\ui\`. Extracting only reads the
game's archives. Then Claude reads the MXML.

Extract the inventory page UI files with NMS Mod Tool (Nexus #4312) into `../work/`, clone
the Starship tab as a "Repairs" tab in an EXML patch, and from code set its text and catch a
button press. Keep the first option that works (it was (a), done by code instead of EXML):

- (a) a full new tab next to Exosuit / Starship
- (b) a native "REPAIR ALL" button and summary on the existing Starship tab
- (c) a hotkey plus the game's own on-screen message and confirmation

Owner installs each test build into `GAMEDATA\MODS\NMSTracker\` and sends screenshots.

## Differences from NMS.py (build differences, not NMS.py bugs)

Everything here was measured on game build **179666**. NMS.py 180132.0 targets build **180132**,
so a different offset is expected, not a mistake in NMS.py. monkeyman192 confirmed on 2026-09-30
that the frontend manager is `Data + 0x859090` in 180132; in 179666 it is `Data + 0x849020`. Check
each of these on 180132 before reporting anything upstream (and only with the owner's OK):

1. Ship tech inventories: `cGcPlayerState + 0xAB28` in 179666 (cargo array 0x8FC8 +
   12 x 0x248); NMS.py has `mShipInventoriesTechOnly` at 0xAA28.
2. `GetInventory(group)` (RVA 0x47BDB0, called with `playerState + 0x900`): group 5 is the
   ship's tech inventory and 6 its cargo in 179666; NMS.py's `InventoryChoice` has
   `Ship_Cargo = 5`, `Ship_Tech = 6`.
3. `AddTimedMessage` takes 11 arguments in 179666; NMS.py lists 10.

Also noted on 179666: 21 of NMS.py's 377 signature hooks don't match (mostly render and update
hooks, none that this mod uses), and 5 match more than once. That is expected for a build NMS.py
wasn't made for.

## Results

| Spike | Date | Game build | NMS.py version | Result | Notes |
|---|---|---|---|---|---|
| S1 | 2026-09-29 | 179666 | 180132.0 | Pass | Ran on slot 1 (ship 2). All 12 damaged entries, costs and damage markers match the save file exactly; tech inventory at 0xAB28 confirmed (holds HYPERDRIVE, LAUNCHER, SHIPJUMP1...). |
| S2 | 2026-09-29 | 179666 | 180132.0 | **Pass (GO)** | Run 4 (v0.4, slot 2): the button called the game's RepairTechnology on R0_SHIPJUMP1 (last pulse engine step): returned True, took exactly 1 CARBON_SEAL. The game's own save 22 s later shows SHIPJUMP1 DamageFactor 1.0 -> 0.0 (charged 100), seals 1 -> 0, RepairTechBuffer 1 -> 0 entries (compared with the pre-repair backup, read-only). Backups of slots 1 and 2 made. Caveat: flags were mirrored from an Analysis Visor repair (fullCost=True) while the game uses fullCost=False for ship steps; same cost here (1 seal), rule to be taken from the game. Earlier runs: group 24; enum `int()` bug; `this` typing bug. |
| S3a | 2026-09-29 | 179666 | 180132.0 | Partial | Run 1 (v0.1): the loader is called ~174k times (cached reloads), so the 80-tree cap filled with boot-time SLOT copies. The load list shows the inventory uses `UI/InventoryPage.mXml` (a text MXML page), `UI/COMPONENTS/POPUPTAB.MBIN` loaded 7x (one per tab, so the tab row is built by code), `UI/MaintenancePage.mXml` + `UI/COMPONENTS/TECH/TECHREPAIRSECTION.MBIN` for the repair screen. v0.2: one tree per file, counts only, live re-read of those pages on F8. Run 2 (v0.2): the tab row is `UI/COMPONENTS/PAGESELECTBARSUB.MBIN` (TABTITLE > TABGROUP > TABCONTAINER > TABS with 13 Text_Special slots OPTION1..13 + OPTIONn_OFF, PREV_PAGE/NEXT_PAGE): spare slots exist, the code shows and labels them. `UI/INVENTORY.MBIN` is the 48-slot grid. InventoryPage and MaintenancePage share one page object, so the last F8 (in the repair screen) overwrote the live inventory snapshot. v0.3 writes one file per F8. Background static scan started for the code that fills and switches the tab row. |
