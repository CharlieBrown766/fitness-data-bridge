> Created time: 2026-08-16 00:51
> Modified time: 2026-08-25 17:20

# Fitness Data Bridge

This private plugin is the application boundary between the generic Fitness Planner and the workspace-owned personal Fitness database.

It provides:

- Xunji training, food and body-data clients, cache controls and credential slots;
- Xunji local SQLite publication with completed-record protection and exact readback;
- SynFit refresh launch and bounded synchronization polling on the target Mac; an already-running SynFit instance is restarted once before polling so a no-op `open` cannot leave new rows pending;
- Apple Calendar scoped write through EventKit with exact wall-clock reminders,
  per-event default-alert removal, readable prescription summaries and
  independent event-plus-reminder-plus-notes readback that also verifies the
  per-event default-alert suppression state;
- Apple Health overlapping merged-JSON snapshot ingestion, stable cross-snapshot deduplication, legacy ZIP staging and parsing into workspace facts;
- a v5.1 half-Block bridge that publishes all A1+B1 or A2+B2 sessions in one transaction/synchronization/Calendar/readback boundary;
- a legacy next-session route retained only for compatibility.

The Xunji adapter translates rule-model units into Xunji's native storage shape. Warm-up and cooldown movements default to Xunji's repetition-only record type; internal values such as `repetitions`, `warmup`, planning intent labels and provenance markers are not written into user-facing weight or note fields. Paired left/right prescriptions become one Xunji row with `weight`, `leftWeight` and movement-level `singleSide`; refreshed facts retain both side loads and normalized side-set volume.

The plugin contains no personal records or credentials. `fitness-planner` remains the owner of generic rules and validators; the Fitness workspace remains the owner of user intent, capabilities, plans, formal data and runtime material. Canonical workspace paths are `用户/数据接入/`, `数据/`, `状态/`, `计划/`, and `运行/`.

See [PRIVACY.md](PRIVACY.md), [TERMS.md](TERMS.md), and [SUPPORT.md](SUPPORT.md).

Dry-run one complete half-Block release:

```powershell
python scripts/fitness_data_bridge_release.py --workspace <Fitness-workspace> --release <workspace-release.json>
```

Supplying `--write` is not enough by itself. Live publication also requires the target Mac and an exact, unexpired one-time authorization file described in [operations.md](skills/fitness-data-bridge/references/operations.md).

Refresh Apple Health facts from the workspace-owned exports:

```powershell
python -m fitness_data_bridge.apple_health --workspace <Fitness-workspace>
```

When `数据/体况/apple-health/merged/` contains `health-merged-*.json`, the
adapter aggregates all snapshots and removes overlap with the declared stable
key `type + startDate + endDate + value + unit + source`. This preserves
history as 48-hour windows arrive every 12 hours. The latest export day is
treated as partial unless `--keep-current-day` is supplied. If no merged JSON
exists, the adapter falls back to the newest legacy `HealthAll_*.zip` under
`raw/`.
