NMS Tracker Mod - a REPAIRS tab for No Man's Sky
=================================================

What it does
------------
- A REPAIRS tab in the inventory, next to Exosuit / Starship / Multi-Tool, in the game's own
  style. It shows your current ship's damage: what you can repair now, what needs materials,
  what needs its own repair screen, every damaged part, and the materials still needed as
  "have / needed".
- F7 in a damaged part's repair screen: repairs every step of that part you can afford.
- F7 anywhere else: repairs every damaged part of your current ship you can afford.
- Repairs use the game's own repair code at the game's normal cost. Nothing is free and nothing
  is edited by hand.

Before you start
----------------
- No Man's Sky on PC (Steam), on the game build this version supports: 180383.
  The mod switches itself off on any other build (it says so in its log).
- Python 3.9 to 3.13 from python.org (not the Microsoft Store version).
- NMS.py for that build:   python -m pip install nmspy==180383.0
- Your saves are backed up the first time the mod changes or shows anything in a session, to
  %LOCALAPPDATA%\ShadowskeepLLC\NMSTrackerMod\backups\ (the newest 5 per save slot are kept).

Run it (the game must be closed; open cmd or PowerShell)
---------------------------------------------------------
On its own (no extra windows):
    pymhf run "C:\path\to\NMSTrackerMod\nmstracker_mod.py"
The game starts by itself. The mod's log is in NMSTrackerMod\logs\.

Or together with your other NMS.py mods:
    1. Once: pymhf config nmspy   and set the mod directory (if you haven't already).
    2. Copy this whole NMSTrackerMod folder into that mod directory.
    3. pymhf run nmspy
(NMS.py's own window and log settings apply in this case.)

Remove it
---------
Delete the NMSTrackerMod folder. The backups folder above can be deleted too once you no longer
need it.

Warning (from NMS.py)
---------------------
NMS.py is missing a lot of game information, and game updates can break mods that use it.
Responsibility for broken saves is on the user. Try it on a spare save first and keep the
backups.

Source, bugs and ideas: https://github.com/ShadowsKeep/nms-tracker-mod
Licence: MIT (see LICENSE). No Man's Sky belongs to Hello Games; this mod contains no game files.
Support: https://buymeacoffee.com/fxshadowking
