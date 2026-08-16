> Created time: 2026-08-16 09:48
> Modified time: 2026-08-16 12:26

# Fitness Data Bridge 1.0.0

- Renamed the product, plugin ID, marketplace, source package, Skill, commands,
  release paths and receipt namespace to Fitness Data Bridge /
  `fitness-data-bridge`.
- Declared the rename as a breaking first release under the new identity and
  required Fitness Planner 2.0.0.

- Restart SynFit once when it is already running before waiting for newly written rows, preventing a no-op app launch from exhausting the synchronization timeout.
- Translate warm-up and cooldown sets into Xunji's repetition-only record type instead of exposing rule-model units such as `+repetitions` or selecting the user-facing self-weight mode.
- Preserve prescribed hold seconds as a Chinese instruction while recording one completion repetition, rather than changing warm-up or cooldown stretches to the time-only record type.
- Keep dedicated dynamic-warmup actions as ordinary unloaded actions; reserve `setType: 热` for warm-up sets nested inside a main lift.
- Stop exporting internal planning intents such as `warmup` as user-facing Xunji notes unless the prescription supplies an explicit connector note.

## Previous 0.2.0 release

- Added the Fitness v5.1 half-Block release bridge and a dedicated preview/publish command.
- Bound one-time authorization to the entire release revision, ordered session list and replacement window.
- Reused the existing multi-record database and Calendar engines to perform one Xunji transaction, one SynFit launch, one Calendar batch and one unified readback/receipt.
- Added defense-in-depth checks against silent missing comparable kilogram loads.
- Retained the v0.1 single-session route for compatibility.

## 0.1.0

- Added bounded adapters for Xunji, SynFit, Apple Calendar and Apple Health.
- Kept credentials, caches, records, health exports and write receipts in the workspace-owned private database boundary.
- Added the Fitness v5 next-session bridge with hash validation, dry-run default and one-time write authorization.
- Added deterministic public packaging, privacy scanning, marketplace metadata and an installable release layout.
- Reverified the OpenAI plugin and Skill packaging contract on 2026-08-16.
