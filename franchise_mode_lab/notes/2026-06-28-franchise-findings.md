# Franchise Mode Findings

Date: 2026-06-28

## Safe lab setup

Working copies were created under:

- `C:\Users\jesus\OneDrive\Documents\NHL legacy mods\franchise_mode_lab`

Inputs copied from the live environment:

- `inputs\ROSTER 20260626154043`
- `inputs\DYNASTY 20260620125233`
- `inputs\default.xex`
- `inputs\EA SPORTS NHL Legacy Edition.config.toml`

This keeps the live Xenia install and playable saves untouched.

## Key roster finding

The live roster DB already contains modern NHL team records for:

- `VGK` -> code `226`
- `SEA` -> code `228`
- `UHC` -> code `22`
- `UTA` -> code `229`

So roster support is not the core blocker for franchise mode.

## Dynasty container finding

The dynasty file is not a `RosterFile`. Its header is:

- `Dynasty`

The file contains many embedded zlib streams. The first stream begins at offset `0x30` and decompresses to a valid EA `DB` database.

Input dynasty SHA-256:

- `EA9D9095468F52E369A2EFADC48554122294EFBF9E61C5C79C85F2EFD005D8B1`

Decompressed chunk path:

- `working\dynasty_chunk0.db`

## Confirmed franchise-mode limitation in dynasty data

The decompressed dynasty DB contains a team metadata table:

- `sbGR`

That table holds only **30 active NHL-style entries**:

- codes `0..29`
- includes `UHC` at code `22`
- excludes `VGK`
- excludes `SEA`

So the dynasty/franchise layer is clearly instantiating a 30-team active league, even though the source roster can hold more teams.

## Candidate active-league tables

The decompressed dynasty DB also contains at least one 30-row team-keyed table:

- `xLuM` -> 30 rows, `qEfv` codes `0..29`

Mapped codes:

- `0 ANA`
- `1 WPG`
- `2 BOS`
- `3 BUF`
- `4 CGY`
- `5 CAR`
- `6 CHI`
- `7 COL`
- `8 CBJ`
- `9 DAL`
- `10 DET`
- `11 EDM`
- `12 FLA`
- `13 LA`
- `14 MIN`
- `15 MTL`
- `16 NSH`
- `17 NJ`
- `18 NYI`
- `19 NYR`
- `20 OTT`
- `21 PHI`
- `22 UHC`
- `23 PIT`
- `24 STL`
- `25 SJ`
- `26 TB`
- `27 TOR`
- `28 VAN`
- `29 WSH`

This is a likely franchise-only team list or per-team franchise state table.

Another 30-row table exists:

- `RZbd`

It does not expose an obvious `qEfv` field in the first sample but should be treated as another likely active-team table that may need expansion if a true 32-team franchise patch is attempted.

## Current interpretation

Franchise mode is not simply reading the roster team table verbatim.

Instead, the dynasty initializer or save builder appears to:

1. build a 30-team active league template
2. populate dynasty tables keyed only to those 30 teams
3. carry Utah in the legacy slot but ignore Vegas and Seattle

## Most promising patch targets

1. Dynasty initialization logic in `default.xex`
2. The dynasty team metadata table (`sbGR`)
3. The 30-row active team table(s), especially `xLuM`
4. Any schedule / standings / playoff tables that assume 30 active teams

## 32-team data probe

Date: 2026-07-08

A lab-only prototype was built without touching the live Xenia install or live saves:

- `working\dynasty_32team_probe.db`
- `working\DYNASTY_32TEAM_PROBE`

The prototype uses active dynasty slots `30` and `31` for the expansion teams, because the active-team key fields in the franchise DB are 5-bit values and cannot store roster IDs like `226` or `228`.

Patched metadata:

- slot `30` -> `VGK` LAS VEGAS GOLDEN KNIGHTS
- slot `31` -> `SEA` SEATTLE KRAKEN

Patched active-team tables:

- `xLuM` -> expanded from 30 records to 32 records
- `RZbd` -> expanded from 30 records to 32 records

The rebuilt Dynasty wrapper re-parses successfully:

- `sbGR` -> 124 records, capacity 128
- `xLuM` -> 32 records, capacity 32
- `RZbd` -> 32 records, capacity 32

Probe hashes:

- `DYNASTY_32TEAM_PROBE` SHA-256: `A204BA2197A55866FF8887FBDEC5352D917908BD5F46D38312FDE9EC6C6767B4`
- `dynasty_32team_probe.db` SHA-256: `F0F6E15212BFBB8643F770C01E7681388B83CA529FC9DF89289925BD9E18DABE`

File sizes:

- original Dynasty save: `4,579,240` bytes
- rebuilt probe Dynasty save: `4,579,240` bytes
- expanded raw Dynasty DB: `8,421,548` bytes

Validation report:

- `notes\dynasty_32team_probe_analysis.md`

This proves the DB-level 32-team expansion is structurally possible, and the modified DB can be recompressed back into the original Dynasty container without shifting trailing file data.

It does not yet prove the game executable accepts the expanded league. The next risk areas are game logic that may still hard-code 30 teams for schedule generation, standings, playoff qualification, UI lists, or franchise initialization.

## Fresh-franchise 32-team probe

Date: 2026-07-08

A brand-new franchise save was created in Xenia:

- `inputs\DYNASTY 20260708142155`

Analyzer result before patching:

- `VGK` absent from Dynasty metadata
- `SEA` absent from Dynasty metadata
- `xLuM` -> 30 records, capacity 30
- `RZbd` -> 30 records, capacity 30

This confirms the game's Dynasty initializer still creates a 30-team league even when the loaded roster contains Vegas and Seattle.

A fresh-franchise 32-team probe was then built:

- raw DB: `working\dynasty_20260708142155_32team_probe.db`
- rebuilt Dynasty save: `working\DYNASTY_20260708142155_32TEAM_PROBE`
- validation report: `notes\dynasty_20260708142155_32team_probe_analysis.md`

Validation after patching:

- slot `30` -> `VGK` LAS VEGAS GOLDEN KNIGHTS
- slot `31` -> `SEA` SEATTLE KRAKEN
- `xLuM` -> 32 records, capacity 32
- `RZbd` -> 32 records, capacity 32

Installed isolated Xenia test slot:

- `D:\Emulation\xenia_manager\Emulators\Xenia Canary\content\E03000006397D304\454109EC\00000001\DYNASTY 32TEAM FRESH\DYNASTY 32TEAM FRESH`
- `D:\Emulation\xenia_manager\Emulators\Xenia Canary\content\E03000006397D304\454109EC\Headers\00000001\DYNASTY 32TEAM FRESH.header`

Forced replacement tests against the fresh `DYNASTY 20260708142155` slot failed in-game with "save file is damaged and cannot be used." The original fresh save was restored from backup after each test.

Conclusion: save editing is useful for reconnaissance, but it is not the right foundation for a real 32-team franchise mod. The game needs to be changed before Dynasty creation so the league is initialized as 32 teams from the start.

## Game-file pivot

The extracted ISO was inventoried under:

- `D:\Emulation\roms\xbox360\NHL Legacy Edition (USA, Europe) (En,Fr,De,Ru)\extracted`

High-signal front-end and data files were extracted into:

- `working\gamefile_targets`

Important extracted targets:

- `fe\ion\game\screens\dynasty\selectteamscreen.big`
- `fe\ion\game\screens\dynasty\dm3_substituteteam.big`
- `fe\ion\game\screens\dynasty\dm25_tradeplayer.big`
- `fe\ion\game\screens\dynasty\dm29_teammanagement.big`
- `fe\ion\game\screens\dynasty\dm36_playofftree.big`
- `fe\ion\game\screens\dynasty\dm37_calendar.big`
- `fe\ion\game\screens\dynasty\dm50_dynastyhub.big`
- `fe\ion\game\screens\gamemode\gmsselectteam.big`
- `fe\salarydata.bin`
- `fe\tradeai.bin`
- `fe\teamanalysis.bin`

String scan findings:

- Dynasty UI files reference engine-side concepts like `ION_NHLDynasty`, `GetActiveLeagues`, `GetActiveUserTeam`, `CHAIN_DYNASTY_START`, `LEAGUE_TIER`, and `MAX_TEAM`.
- These files look like consumers of franchise state, not the source of the active NHL league template.
- `default.xex` has no readable Dynasty strings in-place, so meaningful executable work likely requires XEX unpack/decompression or Xenia memory patching against loaded code addresses.

Likely patch strategy:

1. Use the FE files to map which screens depend on active league/team lists.
2. Unpack/decompress `default.xex` or use Xenia runtime patching to find the Dynasty initialization path.
3. Locate constants / loops that create 30 active NHL teams and tables matching `sbGR`, `xLuM`, and `RZbd`.
4. Patch initializer to create slots `0..31`, with Vegas and Seattle assigned to the correct divisions.
5. Patch schedule generation, standings, playoff qualification, contracts, trades, and UI consumers as needed.

## Immediate next steps

1. Stop save-file patch attempts except for offline reconnaissance.
2. Acquire or configure an XEX unpack/decompression workflow for `default.xex`.
3. Create a Xenia `.patch.toml` skeleton for title ID `454109EC`.
4. Identify the loaded executable address range and map file offsets to runtime patch addresses.
5. Search the unpacked executable / runtime memory for the 30-team Dynasty initializer.
