# Contributing

Thanks for helping. This is an in-game No Man's Sky mod built on
[NMS.py](https://github.com/monkeyman192/NMS.py) / [pyMHF](https://github.com/monkeyman192/pyMHF):
a REPAIRS tab in the inventory that shows the current ship's damage and repairs it at the
normal material cost. Bug reports, testing on other saves and builds, and code are all welcome.

## Ground rules (the mod's safety model)

These are what make the mod safe to run on real saves; changes that break them won't be merged.

- **Repairs only go through the game's own repair functions** (`RepairTechnology`, after the
  game's own `CanRepairTechnology` says yes). Never "repair" by editing inventory memory or
  save files.
- **Saves are backed up before the mod changes anything**, and the mod never writes save files
  itself.
- **Nothing the game draws for the mod can act on real items.** The REPAIRS grids are
  display-only copies, drawn locked with action mask 0 (see `mod/grids.py`).
- **No extra windows.** pyMHF's GUI and log console stay hidden; the mod talks to the player
  through the game's own on-screen messages.
- **The mod switches itself off on any game build it wasn't checked against** (`GAME_BUILD` in
  `mod/game.py`).

## Setting up

- Windows, No Man's Sky on Steam, and Python 3.9 to 3.13 from python.org (not the Microsoft
  Store build).
- `python -m pip install -r requirements.txt`. NMS.py is made for one game build, so its version
  has to match the build in `mod/game.py`.
- Run it with the game closed: `cd mod`, then `pymhf run nmstracker_mod.py`. Logs go to
  `mod/logs/`.

## Tests

```
python -m unittest discover -s tests
```

They run offline, without the game. Game memory is faked in ctypes buffers, and every game call
is intercepted just before it would reach NMS.exe, after ctypes has converted its exact
arguments. One test also checks the installed NMS.exe build, if it finds the game (set `NMS_EXE`
to your `NMS.exe` if it isn't at the default Steam path); otherwise it is skipped. GitHub Actions
runs the suite on every push and pull request.

## Game functions

- **Use NMS.py's declarations first.** Only declare a function the mod needs when NMS.py doesn't
  have it yet, and then in `mod/nms_ext.py`, the way NMS.py does: on its game class, with `this`
  typed as `"_Pointer[<that class>]"` (page helpers as static `cGcFrontendPageFunctions`
  functions taking the page first), found by a byte pattern that matches exactly once, with the
  RVA and what it does in a comment.
- Those declarations are meant to move into NMS.py over time; if you know a function's real
  name, a fix is very welcome.
- Read the game's structures with NMS.py's classes. A raw offset is only for something NMS.py
  doesn't map yet (or maps differently for the current build), with a comment saying where it
  was measured.

## When the game updates

1. Update NMS.py to the release for the new build and pin it in `requirements.txt`.
2. Check that every byte pattern in `mod/` still matches exactly once.
3. Re-check every data offset that carries a build comment (`mod/game.py`, `mod/tab.py`,
   `mod/screen.py`, `mod/grids.py`): where NMS.py now has the field, use it instead.
4. Bump `GAME_BUILD`, run the tests, then test in game: the tab (click, A/D, Esc, clicking
   Starship), the screen and grids on a damaged ship, F7 in a repair screen and elsewhere, and
   compare the save before and after.

## What not to commit

- Game files, extracted game data, or disassembly of the game.
- Save files, or anything read out of a save.
- Logs with your user name or Steam ID in them. `test-logs/` holds cleaned copies, with the
  profile path replaced by `%USERPROFILE%`.

## Pull requests

- Keep them small and focused, and match the code around them.
- Say which game build and NMS.py version you tested on, and what you tested in game (offline
  tests alone don't cover hooks).
- By contributing you agree your work is released under the [MIT licence](LICENSE).

## Reporting bugs

Use the bug report form. It asks for the game build, the NMS.py and pyMHF versions, and the log
from `mod/logs/` (remove your user name from the paths). Please don't attach save files publicly.
