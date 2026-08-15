> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 00:51

# Operations

## Preview next session

Run `scripts/fitness_connector_session.py --workspace <Fitness>`. Add `--database <Xunji.db>` only on a device with a safe local read-only target. The default output is a projection and performs no live write.

## Publish next session

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
