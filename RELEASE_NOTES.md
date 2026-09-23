> Created time: 2026-08-16 09:48
> Modified time: 2026-09-24 00:53

# Fitness Data Bridge 1.0.11

- Reads active, completed and deleted Xunji records for execution reconciliation; Calendar remains reminder-only.
- Links publication identities and traceable user-confirmed successors without changing lifecycle.
- Counts supported drop segments and flags incomplete semantics; detects exact completed duplicates without deduplicating future copied plans.
- Exports versioned action candidates with duplicate-key provenance.

## Previous release documentation

- Preserves action-specific cooldown write semantics instead of forcing every
  cooldown movement into a repetition-only Xunji record.
- Projects prescribed stretch seconds into Xunji's `time` field while keeping
  the human-readable duration instruction in the action note.
- Adds regression coverage proving dynamic warm-ups remain repetition-only and
  cooldown stretches retain `exetype=stretch` with the exact duration.

## Previous 1.0.8 release

- Corrects paired left/right publication for Mac-local Xunji rows by writing
  `left_weight`, while retaining `leftWeight` as the API/watch transport alias.
- Preserves left-side facts from local, API, watch and mixed legacy records,
  including rows where a blank local alias accompanies a populated transport
  alias.
- Adds regression coverage for exact Mac-local projection and both supported
  refresh shapes.
- Preserves completed sessions during later half-Block revisions and projects
  only the remaining scheduled sessions into Xunji and Calendar replacement
  scopes.

## Previous 1.0.7 release

- Added first-class ingestion for overlapping `health-merged-*.json` Apple
  Health snapshots while retaining the legacy `HealthAll_*.zip` route.
- Aggregates all workspace-owned merged snapshots and removes 12-hour/48-hour
  overlap with the stable source record key before daily metrics are computed.
- Normalizes ISO timestamps, numeric sleep stages and percentage oxygen
  saturation, and treats the latest export day as partial by default.
- Keeps personal exports out of the plugin by covering the new format with
  synthetic fixtures only.
- Projects paired left/right prescriptions into Mac-local Xunji `singleSide`,
  `weight` and `left_weight` fields, accepts the API/watch `leftWeight` alias
  during refresh, and preserves side-level volume in refreshed facts.
- Keeps Xunji training experience free of provenance markers, exports only
  explicit human-readable action instructions, and records session-to-Xunji
  identity mappings in publication receipts.
- Stabilizes Mac-local SynFit refreshes before facts become planning-eligible,
  separates training-experience and action-note feedback, and returns output
  hashes for verified cross-device transfer.

## Previous 1.0.6 release

- Emits only the `shujian-component-release` payload and attestation; the
  bridge no longer generates a marketplace or component installer.
- Names `shujian-agent` as the sole marketplace owner and adds release tests
  proving that component self-signing artifacts are absent.
- Locks the component attestation to Fitness Planner 2.0.5 or newer.

## Previous 1.0.5 release

- Added the component-release and Shujian Agent delivery contracts so the
  bridge can only enter the developer Codex through the composite package.
- Prevented validator imports from adding Python bytecode caches to otherwise
  deterministic public packages.

## Previous 1.0.4 release

- Adopted `用户/数据接入/`, `数据/`, `状态/`, and `计划/` as the
  canonical Fitness workspace paths while keeping `运行/` unchanged.
- Added read compatibility for the previous workspace directory names so the
  connector can be installed before the workspace migration.
- Updated agent contracts, the connector skill, Apple Health destinations,
  workspace discovery, tests, privacy guidance, and release metadata.

## Previous 1.0.3 release

- Set EventKit's per-event default-alarm suppression state before saving
  repaired or newly published Calendar events, so a calendar-level default
  alert cannot remain alongside the projected alerts.
- Added the suppression state to Calendar inspection, repair previews and
  exact readback; a repair cannot report success while an inherited default
  alert remains.
- Kept Calendar mutation scoped to the exact authorized release events without
  changing the `Workout` calendar's global default-alert setting.

## Previous 1.0.2 release

- Replaced Calendar mutation and verification with an EventKit batch that
  replaces explicit alerts with the two exact projected wall-clock timestamps.
- Replaced internal phase, block, session and prescription-hash metadata in
  Calendar notes with readable Chinese prescription summaries grouped by
  warm-up, main training, core and cooldown.
- Expanded the release-scoped repair to update notes and alarms together, with
  schema-1.1 one-time authorization and exact EventKit readback.
- Retained the AppleScript generators as compatibility diagnostics, while live
  publication and repair now require EventKit Full Calendar Access.

## Previous 1.0.1 release

- Added exact same-day wall-clock reminder configuration through
  `reminder_times`, including multiple reminders per Calendar event.
- Fixed the AppleScript alarm-unit bug that expanded 30 minutes into 1,800
  minutes, and retained the legacy relative-minute field without scaling.
- Added in-place, release-scoped Calendar reminder repair with one-time
  authorization, event identity preflight and alarm timestamp readback.
- Extended unified Calendar verification to fail when reminder timestamps do
  not match, rather than checking only title, start time and duration.

## Previous 1.0.0 release

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
