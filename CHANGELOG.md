# Changelog

## 0.7.0 (2026-10-04)

First public release, for No Man's Sky build **180383** with **NMS.py 180383.0**.

- **REPAIRS tab** in the inventory tab row, in the game's own style; select it by clicking or
  with Q/E (A/D).
- **REPAIRS screen**: the Starship page's right-hand panel shows what can be repaired now, what
  needs materials, what needs its own repair screen, and your biggest shortfall as have / needed.
  The two grids show every damaged part (with the game's damage markers) and every material the
  damage still needs, as "have / needed", drawn locked so nothing in them can be moved or used.
- **F7** in a damaged part's repair screen repairs every step of that part you can afford; F7
  anywhere else repairs every damaged part of your current ship you can afford. Repairs go
  through the game's own repair code at the normal cost, and parts whose repair was started
  step by step are left for their repair screen, so nothing is paid twice.
- Saves are backed up the first time the mod shows or changes anything in a session.
- Messages use the game's own on-screen notifications, in the game's language. No extra windows.
- Works in NMS.py's mod folder (`pymhf run nmspy`) or on its own (`pymhf run nmstracker_mod.py`).

Known limits:
- A material you have none of, and product materials (e.g. Wiring Loom), show no count in their
  grid slot; the right-hand panel and the log still give have / needed.
- No hover tooltips on the REPAIRS grids (the grid is locked to drawing only).
- F7's whole-ship repair has only run on ships where nothing was affordable in place so far.
- The mod switches itself off on any game build other than 180383.

## Before 0.7.0 (not released)

- 0.6: REPAIRS grids (display-only copies of the damaged parts and materials) on build 179666.
- 0.5: the REPAIRS screen in the Starship page's own panel; materials as have / needed.
- 0.4: the REPAIRS tab built into the mod; messages and item names through the game.
- 0.3: F7 in a repair screen finishes parts (verified by save diffs).
- Spikes S1 (reading the ship), S2 (one repair through the game's own function), S3 (the tab).
