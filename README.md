# NHL Legacy Roster Editor

Yes, we can build a PC program for editing `NHL Legacy Edition` rosters from Xbox 360 saves.

The good news is that this has already been proven possible by older community tools, which means the project is realistic. The hard part is making a modern, reliable workflow for:

1. Opening the Xbox 360 `CON`/`STFS` save container.
2. Extracting the internal roster database.
3. Understanding and editing the EA roster data safely.
4. Repacking the save without corrupting it.

## What We Know

- Xbox 360 roster saves are typically stored in `CON` packages, which are a type of `STFS` content container.
- Older NHL modding tools and forum posts show that Xbox 360 roster files can be extracted, edited, and repacked.
- Artem Khassanov's NHL tools document that newer NHL console rosters use EA `TDB` database files.

## Practical Plan

### Phase 1: Inspect and extract

Goal: reliably detect and inspect Xbox 360 roster containers on PC.

- Read the package header.
- Confirm `CON` / `PIRS` / `LIVE`.
- Locate likely embedded roster payloads.
- Export data for analysis.

### Phase 2: Map the roster database

Goal: identify player/team tables and fields.

- Compare two roster files with known changes.
- Find player names, ratings, jersey numbers, handedness, contracts, etc.
- Build a schema map.

### Phase 3: Safe editing

Goal: edit values and write them back.

- Validate field sizes and offsets.
- Preserve container integrity.
- Add sanity checks to avoid broken saves.

### Phase 4: Friendly desktop UI

Goal: provide a real editor instead of hex tools.

- Search players
- Edit ratings and bio data
- Edit rosters/lines
- Import/export CSV

### Phase 5: Smart real-world sync

Goal: make roster maintenance much faster than doing it in-game.

- Pull current official NHL team rosters from the public NHL web API.
- Pull NHL.com trade coverage headlines for recent move context.
- Compare official roster assignments with your local roster database.
- Show suggested moves like `Player X: TOR -> PHI`.
- Let you accept/reject moves in batches.

### Phase 6: Weighted overall planner

Goal: support the kind of rating workflow you described.

- Pick a player archetype like `sniper` or `playmaker`.
- Set a target overall cap, for example `83 -> 87`.
- Show how many rating points are available to spend.
- Weight key stats more heavily for overall, such as offensive and defensive awareness.
- Drive future UI sliders from the same budget engine.

### Phase 7: Contract normalization

Goal: keep NHL Legacy contracts aligned with the modern NHL cap even when the in-game cap is lower.

- Set the real NHL cap upper limit for the target season.
- Set the in-game salary cap used by NHL Legacy.
- Scale a player's contract by cap-hit percentage instead of copying raw dollars.
- Preserve contract length unless a real extension/signing changed the term.

## Included Starter

This repo now includes a small Python CLI that can:

- Inspect Xbox 360 `STFS` containers
- Inspect Xenia-exposed `RosterFile` saves directly
- Detect the compressed roster payload inside a `RosterFile`
- Extract the decompressed database payload to a separate file
- Fetch current official NHL rosters by team
- Fetch recent NHL.com trade coverage headlines
- Plan weighted overall upgrades for future slider-based editing
- Scale contracts by cap-hit percentage into the NHL Legacy cap environment

It is intentionally a first step, not a full editor yet.

There is now also a local web app shell with:

- workspace creation from a Xenia `RosterFile`
- player search
- inferred current-team lookup
- manual team moves against the player instance row
- NHL.com trade context
- HockeyDB profile lookup for height/weight reference
- contract scaling planner
- create-player comparison planner
- final review log of app-driven edits

## Run It

```powershell
python -m src.nhl_legacy_editor.cli inspect "C:\path\to\your\roster.con"
```

For a Xenia roster file:

```powershell
nhl-legacy-editor inspect "D:\Emulation\xenia_manager\Emulators\Xenia Canary\content\E03000006397D304\454109EC\00000001\ROSTER 20260611193707\ROSTER 20260611193707"
nhl-legacy-editor extract-db "D:\Emulation\xenia_manager\Emulators\Xenia Canary\content\E03000006397D304\454109EC\00000001\ROSTER 20260611193707\ROSTER 20260611193707"
nhl-legacy-editor fetch-roster TOR -o ".\backups\toronto_roster.csv"
nhl-legacy-editor fetch-trades --limit 5
nhl-legacy-editor plan-overall power_forward 87 --stat offensive_awareness=83 --stat body_checking=84 --stat strength=85 --stat puck_control=82 --stat wrist_shot_power=83 --stat hand_eye=82 --stat aggressiveness=80 --stat defensive_awareness=79
nhl-legacy-editor contract-scale "Matthew Knies" --real-cap 104.0 --game-cap 78.6 --cap-hit-percent 0.14
nhl-legacy-editor contract-scale "Matthew Knies" --real-cap 104.0 --game-cap 78.6 --real-aav 7.75
nhl-legacy-editor workspace-open "D:\Emulation\xenia_manager\Emulators\Xenia Canary\content\E03000006397D304\454109EC\00000001\ROSTER 20260611193707\ROSTER 20260611193707"
nhl-legacy-editor app
```

Or after install:

```powershell
python -m pip install -e .
nhl-legacy-editor inspect "C:\path\to\your\roster.con"
```

## Feature Direction

The strongest version of this editor would use two separate real-world data modes:

1. `Roster sync mode`
   Compare the in-game roster against the current official NHL team rosters and suggest moves automatically.

2. `Transaction context mode`
   Show the latest NHL.com trade headlines so you can understand why a suggested move appeared.

For player editing, the weighted-overall planner should be the basis for the UI:

- Archetype dropdown
- Current overall
- Target overall cap
- Remaining points counter
- Sliders for weighted stats
- Lock/unlock specific attributes

For contracts, the editor should support a salary-cap normalization mode:

- Use the real NHL cap for the chosen season.
- Use the actual in-game NHL Legacy cap.
- Recalculate AAV by cap-hit percentage.
- Keep term unchanged unless there was a real new contract.

## What We Need Next

The next real unlock is mapping the decompressed payload schema so we can connect these new features to the actual NHL Legacy database:

- Match official NHL player names/IDs to in-game players
- Identify player team assignment fields
- Identify player type/archetype fields
- Identify rating/stat fields and overall logic
- Write changes back into the roster file safely

## Current Move Breakthrough

The biggest roster-editing roadblock is now partly solved for team moves:

- the main player-instance row lives in table `ulGe`
- field `BSXd` appears to hold the current team code for that instance
- those codes line up with the team table values for clubs like `TB = 26` and `TOR = 27`
- a controlled Darren Raddysh test successfully changed `BSXd` from `26` to `27`
- the edited DB was then repacked back into a valid `RosterFile`

That means the app can now make at least some real player team moves in a working roster copy, with a review trail showing the `BSXd` field change before and after.

## Sources

- [Artem Khassanov NHL tools](https://www.artemkh.com/nhl/)
- [Artem developer tools / TDB docs](https://www.artemkh.com/nhl/devtools/)
- [Free60 STFS overview](https://free60.org/STFS/)
- [Operation Sports NHL roster tools thread](https://forums.operationsports.com/forums/forum/hockey/ea-sports-nhl/ea-sports-nhl-legacy/590157-nhl-14-roster-tools)
- [Official NHL roster API example](https://api-web.nhle.com/v1/roster/TOR/current)
- [Official NHL trade coverage topic page](https://www.nhl.com/news/topic/trade-coverage/)
- [Official 2025-26 NHL Trade Tracker](https://www.nhl.com/news/2025-26-nhl-trades)
