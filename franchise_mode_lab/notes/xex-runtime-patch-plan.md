# XEX / Runtime Patch Plan

## Goal

Patch NHL Legacy so Dynasty mode initializes as a 32-team NHL league before a save file is created.

## Confirmed Context

- Title ID: `454109EC`
- Xenia module hash: `38EB05543CF3ADD2`
- Executable original PE name from Xenia log: `nhlzf.exe`
- Loaded module range from Xenia log: `83E02B80-83E971FE`
- Xenia patch file: `D:\Emulation\xenia_manager\Emulators\Xenia Canary\patches\454109EC - NHL Legacy Edition.patch.toml`
- `apply_patches = true` is already enabled in the global and NHL-specific Xenia configs.

## Why Save Patching Stopped

Fresh Dynasty save patching produced a structurally valid DB but failed in-game as damaged. The likely blocker is container/integrity handling. More importantly, save edits happen too late: Dynasty has already been initialized as a 30-team league.

## Patch Targets

The real initializer must create:

- 32 active NHL team slots, not 30
- Vegas in active slot `30`
- Seattle in active slot `31`
- Correct divisions/conferences at creation time
- Schedule/standings/playoff structures that accept 32 teams
- Trade, free-agent, contract, and salary-cap views that include both teams

## Data Clues From Save Recon

Fresh Dynasty still creates:

- `sbGR` metadata with no `VGK` or `SEA`
- `xLuM` with 30 records
- `RZbd` with 30 records

The DB fields can represent active slots `0..31`, so the data format can plausibly hold a 32-team league if the initializer creates it correctly.

## Front-End Clues

Extracted UI files under `working\gamefile_targets` reference:

- `ION_NHLDynasty`
- `GetActiveLeagues`
- `GetActiveUserTeam`
- `CHAIN_DYNASTY_START`
- `LEAGUE_TIER`
- `MAX_TEAM`

These look like consumers of engine state rather than the source of the league template.

## Needed Tooling

Preferred:

- XEX unpack/decompress tool to produce a PowerPC binary for static analysis.
- Ghidra/IDA/rizin with PowerPC support.

Fallback:

- Xenia runtime memory patching plus Cheat Engine / debugger memory scans.
- Search loaded memory for active team arrays and the 30-team initialization loop.

## First Runtime Search Ideas

Search during Dynasty team selection / new Dynasty creation for:

- 30-team active slot sequence: `00 01 02 ... 1D`
- 30-team team IDs in active order: `ANA, WPG, BOS, ... WSH`
- Constants: `30`, `29`, `82`, `16`, `8`, `4`, `3`
- Active DB table names or values once save creation is triggered: `sbGR`, `xLuM`, `RZbd`

## Patch File Status

The Xenia patch file is currently a disabled stub only. Do not enable it until real addresses and values are verified.

## Static XEX Scan Findings

Unpacked executable:

- path: `working\nhlzf_unpacked.exe`
- image base: `0x82000000`
- SHA-256: `966ACDA47607646AE87C96FAB50B66AC3378CFC3548E72C8E8DD409DAB9D26B1`

New helper scripts:

- `scan_xex_runtime.py` parses the unpacked PE, maps file offsets to runtime VAs, finds string anchors, and ranks nearby PowerPC immediates.
- `summarize_xex_scan.py` prints the JSON scan report.
- `dump_ppc_context.py` dumps lightweight PowerPC context around candidate addresses.
- `find_address_refs.py` traces direct and `lis/addi` style references to runtime addresses.

High-signal scan outputs:

- `working\xex_runtime_scan.json`
- `working\xex_precise_league_scan.json`

Important string anchors found in the executable:

- `FeLeagueCreator::AvailableTeam` at `0x82047A74`
- `LeagueTeamInfoReader` at `0x82048E24`
- `FeLeagueManager::CreateLeague::numTiers` at `0x8204AC6C`
- `BaseLeague` at `0x8204EFC0`
- `NumberOfTeams` at `0x8204F1C4`
- `GameSpreadLogic::CombineAndWriteSchedule::allGames` at `0x82054158`

Candidate 32-team league creation sites:

- `0x829D1CFC`: `addi r5,r29,30` (`0x38BD001E`) inside a loop that calls `0x82AF0368` with `r4 = r29` and `r5 = r29 + 30`.
- `0x829D1D10`: `cmpwi r29,30` (`0x2F1D001E`) controlling that same loop.
- This is currently the best candidate for stock 30-team NHL/AHL or primary/secondary team pairing during `FeLeagueCreator::AvailableTeam` setup.
- If this really is the active team-pair builder, a 32-team patch probably needs both immediates changed to `32`: `0x38BD0020` and `0x2F1D0020`.

Candidate team-count validation site:

- `0x82AFD890`: `cmpwi r11,30` (`0x2F0B001E`) in a `LeagueTeamInfoReader` path.
- The surrounding code counts/collects up to 60 team-like records, stores the resulting count at offset `4(r31)`, and then compares that count to 30.
- Based on nearby branch patterns, this may be a "must have at least 30" validation rather than a max cap. For 32-team Dynasty it may need to become `0x2F0B0020`.

Current caution:

- These addresses are promising but not sufficient. We still need to confirm whether a three-write test produces 32 team choices or whether additional Dynasty DB, division, schedule, and playoff code paths clamp back to 30.
- Do not enable candidate writes on the main playable install until we are ready to create a separate Xenia test profile or can quickly revert the patch file.
