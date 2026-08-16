> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 10:51

# Operations

## Preview half-Block release

Run `scripts/fitness_connector_release.py --workspace <Fitness> --release <workspace-relative-release.json>`. Add `--database <Xunji.db>` only on a device with a safe local read-only target. The default output is one complete batch projection and performs no live write.

## Publish half-Block release

Live publication requires `--write --authorization <file>` on the target Mac. The schema-2.0 authorization binds `phase_id`, `block_id`, `release_id`, `revision`, `release_sha256`, the ordered `session_ids`, the exact `replacement_window`, `confirmed: true`, `one_time: true`, and a timezone-aware `expires_at`.

All strength sessions are written in one Xunji transaction, SynFit is launched once, and all release events are written to Calendar in one batch. Database and Calendar are read back as one release before one receipt is finalized. Do not invoke the legacy session route repeatedly.

## Legacy next-session compatibility

Run `scripts/fitness_connector_session.py --workspace <Fitness>`. Add `--database <Xunji.db>` only on a device with a safe local read-only target. The default output is a projection and performs no live write.

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

Strength sessions are written to Xunji SQLite, synchronized through SynFit and published to Apple Calendar. Cardio and recovery sessions publish only their Calendar event. Receipts are stored under `运行/receipts/connector/`. A partial failure retains before/after snapshots and writes a partial receipt; do not retry it as a fresh write.

## Xunji API and facts

Use the narrow module entrypoint matching the requested domain: `xunji_client`, `xunji_plan`, `xunji_food`, `xunji_body`, `xunji_bulk_fetch`, or `facts_refresh`. Reads may populate workspace caches and facts. Mutations require their native dry-run and confirmation gates.

## Apple Health

Use `python -m fitness_connector.apple_health --workspace <Fitness> ...`. Raw exports land in `事实/体况/apple-health/raw/`; parsed outputs land in `事实/体况/apple-health/parsed/`.
