

> Windows roster editor for NHL Legacy Edition, tested exclusively with Xenia. Safely edit players, teams, ratings, contracts, and roster assignments with automatic backups and validation. Franchise draft-pick ownership and other engine-level modifications are not currently supported.

And here is a detailed, copy-ready README:

# NHL Legacy Roster Editor

A Windows desktop application for inspecting and editing NHL Legacy Edition roster files.

> **Compatibility notice:** This application has only been tested with NHL Legacy Edition running through Xenia Canary on Windows. It has not been tested on a physical Xbox 360, other emulators, or PlayStation versions of the game.

The editor works with Xenia-exposed `RosterFile` saves. It extracts the embedded EA roster database, creates a separate working copy, applies supported edits, validates the resulting database, and rebuilds the roster container.

The project does not include NHL Legacy Edition, copyrighted game archives, roster saves, player photographs, or Xbox system files. Users must provide their own legally obtained game and roster files.

## Current status

The editor is usable for supported roster changes, but it is not a complete editor for every NHL Legacy or Franchise Mode system.

Some screens provide working roster-editing tools. Others are research, planning, or read-only reference tools. Features that cannot yet be written safely are identified below.

## Working and validated features

### Xenia roster support

The editor can:

- Open Xenia `RosterFile` saves.
- Detect and decompress the embedded EA roster database.
- Create a separate editing workspace.
- Preserve the original roster while changes are being prepared.
- Rebuild a valid `RosterFile` after supported edits.
- Validate the rebuilt roster structure.
- Maintain an edit-review trail.
- Create backups before replacing files.

The primary tested format is a roster saved by NHL Legacy Edition under Xenia Canary.

### Player search and roster inspection

Users can:

- Search for players by name.
- View player IDs and database records.
- Inspect current club and organization assignments.
- Distinguish NHL, minor-league, prospect, and inactive player relationships.
- Review bio, rating, contract, and team-assignment information.
- Compare local players with available real-world reference information.

### Player movement

Supported player movement includes:

- Moving an existing player between mapped NHL organizations.
- Updating the player’s primary team-assignment record.
- Preserving unrelated player records.
- Showing the before-and-after team values in the review screen.
- Writing the supported move back into the roster database.

Team movement has been tested using the player-instance team field used by NHL Legacy.

This does not change the game’s hardcoded league size or Franchise Mode structure.

### Player information and ratings

The desktop application includes tools for reviewing and editing supported player information, including:

- Player names and biographical information.
- Jersey numbers.
- Positions and player types.
- Skater ratings.
- Goaltender ratings.
- Player overall planning.
- Potential and development-related values where mapped.
- Contract-related values where mapped.

The application includes guardrails intended to prevent invalid field sizes and obviously unsafe values.

### Overall-rating tools

The editor includes weighted rating tools that can:

- Estimate a player’s overall rating.
- Plan rating changes toward a target overall.
- Use different player archetypes.
- Give greater weight to important attributes.
- Compare a target player with selected NHL players.
- Preview rating changes before applying them.

The calculated result is an estimate based on the mapped NHL Legacy attributes. It should be reviewed in-game after saving.

### Contract tools

The editor can help with:

- Viewing mapped contract information.
- Comparing contract data with available real-world references.
- Scaling contracts to NHL Legacy’s lower in-game salary-cap environment.
- Preserving contract length when appropriate.
- Preparing contract updates for review.
- Applying supported contract-field changes to a roster workspace.

Real-world contract amounts can be normalized by salary-cap percentage instead of being copied directly into the game.

Contract data from external websites may change or become unavailable. Users should review all proposed values before applying them.

### Current-roster reference tools

Where an internet connection is available, the application can retrieve or display information from sources such as:

- The official NHL roster API.
- NHL transaction and trade coverage.
- HockeyDB player profiles.
- CapWages contract information.
- MoneyPuck-derived statistical data used by supported comparison tools.

External data is used as reference material. The editor does not guarantee that automatically matched players are correct, so proposed changes should be reviewed before they are written.

### Backups and review

The editor is designed around reversible editing:

- Original roster files are preserved.
- Work is performed in a separate workspace.
- Important operations create backups.
- Changes can be reviewed before rebuilding the roster.
- Supported operations validate the temporary database before it replaces a working copy.
- The final review screen records app-driven changes.

Keep additional manual backups of important roster and Dynasty saves.

## Available but experimental

The following features are implemented or partially implemented but should be treated as experimental.

### Bulk roster synchronization

The editor can compare the local roster with current NHL organization data and prepare batches of:

- Team changes.
- Contract changes.
- Player updates.
- Potential create-player candidates.

Automatic matching is not perfect. Players with similar names, stale organization relationships, custom records, or incomplete external data may require manual review.

Do not apply a large automatic update without reviewing every proposed change and retaining a backup.

### Player creation

The application contains tools for creating or synchronizing players using available roster slots and mapped database relationships.

Player creation is more complex than editing an existing player. NHL Legacy contains relationships between bio, rating, team, contract, presentation, and other records. Although the editor validates mapped structures, every created player has not been tested through every game mode.

After creating players, test:

- Roster management.
- Exhibition games.
- Player information screens.
- A newly started Franchise.
- Saving and reloading.

### 2026 draft class

The application includes a 2026 draft-class dataset and tools for:

- Scanning the roster for listed prospects.
- Identifying present or missing prospects.
- Reviewing draft information.
- Creating or synchronizing selected prospects.
- Assigning mapped draft-rights information.
- Applying scouting-based rating profiles.
- Validating the temporary database before installation.

This feature concerns player and prospect records. It is not the same as editing future Franchise Mode draft-pick ownership.

Draft-class creation remains experimental and should be tested on a copied roster before starting a new Franchise.

### Advanced statistical updates

The application contains tools for building rating suggestions from external performance data.

These tools can help prepare rating changes, but they are not an official recreation of EA’s overall-rating formula. Statistical matching and rating translations require user review.

## Not working or not supported

### Franchise draft-pick ownership

Writing traded future draft picks into a Franchise save is not currently supported.

The application may display draft-pick information from CapWages or allow users to record picks in a planning or trade ledger. Those entries are informational only.

They do not alter the underlying NHL Legacy Franchise Mode draft-pick ownership table.

This feature is intentionally disabled because the relevant Dynasty database structure has not been mapped safely enough. Guessing at these records could produce a damaged save or cause the game to crash.

To be clear:

- Viewing real-world draft-pick ownership: available as reference information.
- Recording a draft pick in an app ledger: available for planning.
- Changing ownership of a future pick inside NHL Legacy Franchise Mode: not working.
- Injecting a traded pick into an existing Dynasty save: not supported.

### Existing Dynasty or Franchise saves

The primary supported target is a roster file used before starting a new Franchise.

Existing Dynasty saves contain their own copies of player, team, contract, schedule, and league information. Editing the standalone roster does not automatically update an existing Dynasty.

The editor should not be assumed to support arbitrary Dynasty-save modifications.

### League expansion and 32-team Franchise Mode

The editor does not convert NHL Legacy’s original Franchise Mode into a fully working modern 32-team league.

It does not safely provide:

- A true 32-team Dynasty initializer.
- Fully integrated Vegas and Seattle expansion.
- A rebuilt league alignment.
- A new playoff structure.
- A modern Franchise schedule generator.

Research exists in related projects, but executable patching remains separate from the supported roster editor.

### 84-game schedules

The editor does not change the game’s schedule generator or create a working 84-game NHL season.

Season length and schedule generation appear to require executable-level research outside the roster database.

### Executable and gameplay modifications

This application is not intended to patch `default.xex`.

It does not officially modify:

- Gameplay logic.
- AI behavior.
- Physics.
- Franchise initialization.
- Salary-cap engine logic.
- League-size limits.
- Playoff logic.
- Schedule-generation code.

Experimental executable patches from other projects should not be confused with supported roster-editor features.

### Player portraits

Portrait installation is handled by a separate NHL Legacy modding workflow.

The roster editor may preserve or edit mapped Portrait ID fields, but the editor package does not include player photographs or modified game archives.

Displaying a new portrait requires compatible portrait assets in the game’s `cache.big` and `nocache.big` archives in addition to the correct roster Portrait ID.

### Physical Xbox 360 support

The application has not been validated for deployment to a physical Xbox 360.

Xbox `CON`/STFS containers may require additional extraction, rebuilding, signing, or resigning steps that are not part of the tested Xenia workflow.

## Tested environment

The current tested environment is:

- Windows 11.
- Xenia Canary.
- NHL Legacy Edition for Xbox 360.
- Xenia-exposed `RosterFile` saves.
- Python 3.11 for source installations.

Other configurations may work, but they have not been validated.

## Installation

### Portable Windows package

When a packaged release is available:

1. Download the latest Windows ZIP from the GitHub Releases page.
2. Extract the entire ZIP to a normal writable folder.
3. Run `NHLLegacyRosterEditor.exe`.
4. Do not run the program directly from inside the ZIP.
5. Keep Xenia closed while the editor is rebuilding or replacing a roster.

Windows may display a SmartScreen warning because community builds are not code-signed.

### Running from source

Requirements:

- Windows.
- Python 3.11 or newer.
- The repository’s bundled TDB access libraries.

Install:

```powershell
git clone https://github.com/theharryeagle/NHL-Legacy-Roster-Editor.git
cd NHL-Legacy-Roster-Editor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Start the desktop application:

```powershell
.\.venv\Scripts\nhl-legacy-desktop.exe
```

Display command-line help:

```powershell
.\.venv\Scripts\nhl-legacy-editor.exe --help
```

## Basic Xenia workflow

1. Close Xenia and the roster editor.
2. Locate the roster you want to edit in Xenia’s content directory.
3. Make a manual backup of the entire roster folder.
4. Open the roster in NHL Legacy Roster Editor.
5. Review the detected workspace and player data.
6. Make only supported changes.
7. Review the final change list.
8. Rebuild or install the edited roster.
9. Start Xenia.
10. Load the edited roster inside NHL Legacy Edition.
11. Test roster management and an exhibition game.
12. Start a new Franchise if you want the edited roster incorporated into Franchise Mode.

Do not overwrite an important roster or Dynasty save without a separate backup.

## Command-line examples

Inspect a Xenia roster:

```powershell
nhl-legacy-editor inspect "C:\path\to\ROSTER NAME"
```

Extract its embedded database:

```powershell
nhl-legacy-editor extract-db "C:\path\to\ROSTER NAME"
```

Create an editing workspace:

```powershell
nhl-legacy-editor workspace-open "C:\path\to\ROSTER NAME"
```

Open the desktop editor:

```powershell
nhl-legacy-desktop
```

## Safety recommendations

- Keep Xenia closed while writing roster files.
- Keep the editor closed while another tool modifies the same roster.
- Back up the complete Xenia save folder, including its header.
- Test large changes on a copied roster.
- Start a new Franchise after major roster edits.
- Do not assume a standalone roster change will update an existing Dynasty.
- Treat automatic player matching as a suggestion until reviewed.
- Avoid experimental draft, expansion, or executable modifications on valuable saves.
- Never distribute copyrighted game archives or another user’s roster without permission.

## Test status

The current automated test suite contains 47 passing tests covering supported editor logic such as:

- Attribute and overall calculations.
- Contract normalization.
- Player matching.
- Organization handling.
- Workspace paths.
- Draft-name matching and selected draft-class logic.
- Supported roster data transformations.

Automated tests do not replace in-game testing. The application has only been tested with Xenia.

## Project scope

The goal of this project is to provide a safer and more approachable way to maintain NHL Legacy rosters on Windows while clearly separating validated roster editing from unfinished Franchise Mode and executable research.

Contributions are welcome, particularly for:

- Additional roster-field validation.
- Reproducible Xenia testing.
- Improved player matching.
- Safer created-player workflows.
- Documented Dynasty database research.
- Packaging and installer improvements.

Please do not submit copyrighted game files, player-photo archives, roster saves containing personal data, or Xbox system files.
