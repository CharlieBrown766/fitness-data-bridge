> Created time: 2026-08-16 00:51
> Modified time: 2026-08-19 22:47

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

## Apple Health

Use `python -m fitness_data_bridge.apple_health --workspace <Fitness> ...`. Raw exports land in `数据/体况/apple-health/raw/`; parsed outputs land in `数据/体况/apple-health/parsed/`.
