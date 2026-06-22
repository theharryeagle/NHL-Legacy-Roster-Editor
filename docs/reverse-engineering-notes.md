# Reverse Engineering Notes

Current findings from `backups/roster_20260611193707.db`:

## Confirmed tables

- `ttOk` (table index `0`)
  Team metadata table.
  Confirmed from rows containing:
  - city name in `ITNQ`
  - full team name in `JkmY`
  - abbreviation in `RPbr` and `nnsx`

- `cPbu` (table index `7`)
  Player bio table.
  Confirmed from rows containing:
  - hometown in `JzFM`
  - first name in `PedH`
  - last name in `RMbQ`
  - likely player ID in `zIBw`
  - another long-form ID in `DaPp`

- `ajmx` (table index `5`)
  Linked player data table keyed by `zIBw`.
  Likely flags/archetype/summary-style data.

- `yvSd` (table index `6`)
  Linked player ratings table keyed by `zIBw`.
  Contains many 0-63 style values that look like attributes.

## Likely relation tables

- `caBZ` (table index `2`)
  Three-field relation table:
  - `BERR`
  - `qEfv`
  - `qFky`

  `qFky` references player IDs found in `cPbu`.
  This likely links players to another entity, but current-team mapping is not confirmed yet.

- `FSzD` (table index `9`)
  Larger linked table keyed by `DaPp`.
  Multiple rows per player.

- `vuqu` (table index `8`)
  Small linked table keyed by `DaPp`.

## Useful commands

```powershell
nhl-legacy-editor tdb-tables ".\backups\roster_20260611193707.db"
nhl-legacy-editor tdb-fields ".\backups\roster_20260611193707.db" 7
nhl-legacy-editor tdb-sample ".\backups\roster_20260611193707.db" 0 --limit 2
nhl-legacy-editor player-find ".\backups\roster_20260611193707.db" Knies
nhl-legacy-editor player-snapshot ".\backups\roster_20260611193707.db" Matthew Knies
```

## Next targets

1. Identify which field or relation controls current team assignment.
2. Identify which `yvSd` columns map to visible in-game attributes.
3. Identify which `ajmx` columns control player type/archetype and overall-related metadata.

## Player Type / Fighting Mapping

- Position is stored in `cPbu.aljv`:
  - `0 = C`
  - `1 = LW`
  - `2 = RW`
  - `3 = D`
  - `4 = G`
- Fighting frequency is stored in `yvSd.YqJH`:
  - `0 = Never`
  - `1 = Rarely`
  - `2 = Sometimes`
  - `3 = Often`
- Player type is duplicated in `yvSd.sFgQ` and every linked `ulGe.sFgQ` team/player-instance row.
  - The editor should prefer `yvSd.sFgQ` for display.
  - When saving, sync `yvSd.sFgQ` and every linked `ulGe.sFgQ`; otherwise players such as Auston Matthews can show Sniper in one place and Playmaker in another.
- Forward style codes:
  - `5 = Grinder`
  - `6 = Playmaker`
  - `7 = Sniper`
  - `8 = Power Forward`
  - `9 = 2-Way Forward`
  - `10 = Enforcer`
- Defense style codes:
  - `1 = Defensive Defenseman`
  - `2 = Offensive Defenseman`
  - `3 = Enforcer`
  - `4 = 2-Way Defenseman`
- Goalie style codes:
  - `1 = Hybrid Goalie`
  - `2 = Butterfly Goalie`
  - `0 = Stand-Up Goalie` is likely, but only confirmed from a hidden/no-normal-instance row so far.

## Potential Mapping

- Old NHL 14/15/Legacy roster-tool notes indicate potential is split into two concepts:
  - Growth Letter controls color/accuracy: `A = Green`, `B/C = Yellow`, `D/E/F = Red`.
  - Growth Tier controls stars with half-star increments: `1 = 5.0`, `2 = 4.5/4.6`, down to `10 = 0.5`.
- Confirmed against in-game examples from the current roster:
  - Growth Tier / stars is `yvSd.AMoQ`.
  - Growth Letter / underlying green-yellow-red accuracy is `yvSd.feBm`.
- `yvSd.AMoQ` star mapping:
  - `1 = 5.0`
  - `2 = 4.5`
  - `3 = 4.0`
  - `4 = 3.5`
  - `5 = 3.0`
  - `6 = 2.5`
  - `7 = 2.0`
  - `8 = 1.5`
  - `9 = 1.0`
  - `10 = 0.5`
- `yvSd.feBm` accuracy mapping:
  - `1 = High / Green`
  - `2 = Medium / Yellow`
  - `3 = Medium / Yellow` family, likely alternate B/C letter
  - `4 = Low / Red`
  - `5 = Low / Red` family
  - `6 = Low / Red` family
- Exact/Silver is not a single confirmed growth-letter code.
  - Examples such as Brandon Hagel can display silver in-game while still carrying the underlying yellow value.
  - Treat silver/exact as game-derived/fully-developed display state until a separate override field is decoded.

## Portrait / Headshot Notes

- NHLView NG documentation confirms two separate appearance systems:
  - Portrait ID / Has Portrait controls the 2D player photo used by menus/cards.
  - Head ID / Head Type controls the in-game 3D face/head.
- The app has not updated in-game photos yet. That will require archive/asset editing in addition to roster-field editing.
