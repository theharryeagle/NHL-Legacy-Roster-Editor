# NHL Legacy Xenia Mod Notes

Date: 2026-06-20

## Current local setup

- ISO:
  - `D:\Emulation\roms\xbox360\NHL Legacy Edition (USA, Europe) (En,Fr,De,Ru)\NHL Legacy Edition (USA, Europe) (En,Fr,De,Ru).iso`
- Extracted game folder:
  - `D:\Emulation\roms\xbox360\NHL Legacy Edition (USA, Europe) (En,Fr,De,Ru)\extracted`
- Xenia install:
  - `D:\Emulation\xenia_manager\Emulators\Xenia Canary`
- NHL title-specific Xenia config:
  - `D:\Emulation\xenia_manager\Emulators\Xenia Canary\config\EA SPORTS NHL Legacy Edition.config.toml`
- Current roster baseline:
  - `D:\Emulation\xenia_manager\Emulators\Xenia Canary\content\E03000006397D304\454109EC\00000001\ROSTER 20260611193707`

## Backups created

- Config backup:
  - `C:\Users\jesus\OneDrive\Documents\NHL legacy mods\backups\xenia-config`
- Content backup:
  - `C:\Users\jesus\OneDrive\Documents\NHL legacy mods\backups\xenia-content`

## Xenia graphics changes made

- Set `readback_resolve = "full"` in the NHL-specific config.
- Confirmed these were already set:
  - `disable_context_promotion = true`
  - `gpu = "vulkan"`
  - `mount_cache = true`

## Verified NHL schedule facts

- The NHL is still on an 82-game schedule in the 2025-26 season.
- The 84-game schedule starts in the 2026-27 season.
- Utah is now officially the Utah Mammoth.

## Important modding findings

- The ISO has been successfully unpacked with `xdvdfs`.
- Top-level game content is mostly in EA `.big` archives plus `default.xex`.
- Community guidance for NHL 11 through NHL Legacy indicates:
  - top-level `.big` archives can be extracted with `QuickBMS` + `fightnight.bms`
  - `gamedata/*.big` is usually left alone initially
  - texture/file mods are commonly done by replacing extracted loose folders or rebuilding `.big`

## Likely architecture for the requested mod

There are probably two separate problems:

1. League data problem
   - Team assignments, divisions, logos, rosters, and custom-team mappings may live in roster/save data and/or archive-contained databases.

2. Game logic problem
   - A real 32-team league layout with correct divisions, 84 games, balanced home/away, and a new scheduler likely requires patching `default.xex`.
   - Initial string scans did not expose obvious plain-text division/schedule logic in `default.xex`, which suggests the actual logic is compiled or encoded.

## Current risk assessment

- Updating rosters/divisions is likely feasible.
- Replacing Arizona with Utah and mapping Vegas/Seattle correctly is likely feasible.
- A true 84-game scheduler with hard home/away balancing is much higher risk and may require executable reverse engineering rather than data-only edits.
- Community roster work seen so far often uses custom teams as substitutes, not true engine-level expansion.

## Next recommended steps

1. Unpack the top-level `.big` archives and inventory candidate data files.
2. Identify whether division/team tables are in archive data or only in saves.
3. Determine whether season length and schedule generation are data-driven or hardcoded in `default.xex`.
4. If hardcoded, decide whether to:
   - keep the existing game scheduler and only fix divisions/teams
   - or begin executable patch research for 84-game support
