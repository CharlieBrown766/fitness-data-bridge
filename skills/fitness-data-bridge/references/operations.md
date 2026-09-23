> Created time: 2026-08-16 00:51
> Modified time: 2026-09-24 00:49

# Operations

## Preview half-Block release

Run `scripts/fitness_data_bridge_release.py --workspace <Fitness> --release <workspace-relative-release.json>`. Add `--database <Xunji.db>` only on a device with a safe local read-only target. The default output is one complete batch projection and performs no live write.

## Publish half-Block release

Live publication requires `--write --authorization <file>` on the target Mac. The schema-2.0 authorization binds `phase_id`, `block_id`, `release_id`, `revision`, `release_sha256`, the ordered `session_ids`, the exact `replacement_window`, `confirmed: true`, `one_time: true`, and a timezone-aware `expires_at`.

All strength sessions are written in one Xunji transaction, SynFit is launched once, and all release events are written to Calendar in one batch. Database and Calendar are read back as one release before one receipt is finalized. Do not invoke the legacy session route repeatedly.

Calendar defaults may specify `reminder_times` as unique same-day `HH:MM`
values that precede the event start. The connector writes these as exact alarm
timestamps and verifies them during unified readback. The legacy scalar
`reminder_minutes_before` remains supported and is interpreted directly in
minutes.

## Repair half-Block Calendar notes and reminders

Run `scripts/fitness_data_bridge_calendar_repair.py --workspace <Fitness> --release <workspace-relative-release.json>` on the target Mac for a read-only comparison of exact event identities, current notes and all EventKit alarm timestamps against the projection.

Live repair adds `--write --authorization <file>`. Its schema-1.1 authorization binds the exact release identity and hash, ordered session IDs, replacement window and projection hash with `operation: repair_release_calendar_event_details`, `confirmed: true`, `one_time: true`, and a timezone-aware `expires_at`. The operation changes only notes and alarms in place, sets the per-event default-alert suppression state, replaces explicit alerts with the exact projected alarms, performs one EventKit batch, and writes one notes-and-reminders readback receipt. Readback must verify that suppression state as well as both timestamps and the notes.

EventKit inspection and mutation require Full Calendar Access. Check with `scripts/fitness_data_bridge_calendar_access.py`; add `--request` only while the user is present to approve the macOS permission prompt. If a remote macOS session returns denied without displaying a prompt, stop retrying and have the user grant Full Calendar Access to the responsible remote-login process in System Settings before continuing.

## Legacy next-session compatibility

Run `scripts/fitness_data_bridge_session.py --workspace <Fitness>`. Add `--database <Xunji.db>` only on a device with a safe local read-only target. The default output is a projection and performs no live write.

## Legacy publish next session

Live publication requires `--write --authorization <file>` on the target Mac. The authorization JSON must contain exactly:

```json
{
  "authorization_schema_version": "1.0",
  "operation": "publish_next_session",
  "session_id": "exact-session-id",
  "prescription_sha256": "exact-64-character-hash",
  "scheduled_for": "YYYY-MM-DD",
  "confirmed": true,
  "one_time": true,
  "expires_at": "ISO-8601 timestamp with timezone"
}
```

Strength sessions are written to Xunji SQLite, synchronized through SynFit and published to Apple Calendar. Cardio and recovery sessions publish only their Calendar event. Receipts are stored under `运行/receipts/data-bridge/`. A partial failure retains before/after snapshots and writes a partial receipt; do not retry it as a fresh write.

## Xunji API and facts

Use the narrow module entrypoint matching the requested domain: `xunji_client`, `xunji_plan`, `xunji_food`, `xunji_body`, `xunji_bulk_fetch`, or `facts_refresh`. Reads may populate workspace caches and facts. Mutations require their native dry-run and confirmation gates.

For a multi-week training-history refresh, enumerate every Monday in the exact
requested interval and run `facts_refresh --week-start YYYY-MM-DD` for each
week. Verify that each week-specific facts file exists; `latest_summary.md`
does not establish continuity. Formal Mac-local facts require the post-SynFit
database signature to remain stable for the configured settle interval.

## Review retained Xunji facts

For retained Xunji facts, `python -m fitness_data_bridge.facts_review --workspace <Fitness> --facts <week.json> [<week.json> ...]` recalculates statistics using the hash-bound personal catalog, returns source hashes and traceable feedback, and proposes title-based session matches for review. It writes nothing and does not treat candidates as completed sessions. Use publication receipt identities and user corrections to resolve candidates. `source_sync_eligible` describes the original refresh; `statistics_complete` independently describes classification completeness. Missing historical mappings are personal-data work, not automatic plugin-development requests.

Live refresh retains the prior raw/facts bytes in sibling `history/<sha256>.json` before replacing a weekly latest view. Bind plan evidence to a retained snapshot and hash when reproducibility matters. Normalized fields never authorize rewriting completed Xunji rows.

## Apple Health

Use `python -m fitness_data_bridge.apple_health --workspace <Fitness> ...`.
The default route aggregates every `health-merged-*.json` under
`数据/体况/apple-health/merged/`, removes overlap by
`type + startDate + endDate + value + unit + source`, and writes derived daily
facts under `数据/体况/apple-health/parsed/`. The newest export day is excluded
as partial unless `--keep-current-day` is explicit. If no merged JSON exists,
the adapter falls back to the newest legacy `HealthAll_*.zip` under
`数据/体况/apple-health/raw/`. An explicit JSON or ZIP path remains supported;
the default copies external inputs and `--move-source` must be explicit before
the source is moved into its workspace-owned directory.


## Xunji execution authority

Active Xunji plans are the current execution arrangements; completed records and traceable user corrections establish actual execution. Calendar is reminder-only and may be stale. Never infer missed training from a Calendar mismatch or overwrite Xunji using Calendar. Reminder writeback requires an explicit user request. Preserve original workspace prescriptions and terminal states.

Refresh all relevant natural weeks before reconciliation, including the original and moved dates. Facts schema 5 retains deleted rows and raw action content for lineage review, but excludes deleted rows from workload totals. Exact completed source duplicates are annotated and counted once; future copied plans are never deduplicated. Source records remain intact. The record window is explicit: absence from it does not prove cancellation.

Run `python -m fitness_data_bridge.facts_review --workspace <Fitness> --facts <week.json> [<week.json> ...] --receipts <publication-receipt.json> [<receipt.json> ...]`. Inspect `reconciliation`: receipt IDs link directly, copy/delete successors remain candidates until lineage or user confirmation resolves them. A workspace `--confirmed-links <links.json>` array may contain `session_id`, `record_id`, `original_text`, workspace-relative `source_path` and the SHA-256 of its retained user-correction source as `source_sha256`. This is evidence overlay, never authorization to mutate lifecycle. Review active unfinished arrangements before generating or replacing a release.

Drop segments contribute repetition and load totals without adding main set rows. Unknown nested shapes or missing paired-side semantics mark statistics incomplete. Missing personal mappings list the affected actions and sets; do not infer recording types or per-hand loads from an official key alone.

For an application identity audit, run `python -m fitness_data_bridge.action_export --bundle <main.jsbundle> --app-version <version> --output <new-workspace-candidate.json>`. Output is candidate-only, includes source hash and duplicate keys, and never replaces a Planner registry automatically. Structural extraction is not proof of UI behavior.
