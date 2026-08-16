> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 12:00

# Fitness Data Bridge

This private plugin is the application boundary between the generic Fitness Planner and the workspace-owned personal Fitness database.

It provides:

- Xunji training, food and body-data clients, cache controls and credential slots;
- Xunji local SQLite publication with completed-record protection and exact readback;
- SynFit refresh launch and bounded synchronization polling on the target Mac; an already-running SynFit instance is restarted once before polling so a no-op `open` cannot leave new rows pending;
- Apple Calendar scoped write and independent readback;
- Apple Health export staging and parsing into workspace facts;
- a v5.1 half-Block bridge that publishes all A1+B1 or A2+B2 sessions in one transaction/synchronization/Calendar/readback boundary;
- a legacy next-session route retained only for compatibility.

The Xunji adapter translates rule-model units into Xunji's native storage shape. Warm-up and cooldown movements default to Xunji's repetition-only record type; internal values such as `repetitions`, `warmup`, and planning intent labels are not written into user-facing weight or note fields.

The plugin contains no personal records or credentials. `fitness-planner` remains the owner of generic rules and validators; the Fitness workspace remains the owner of personal intent, capabilities, plans, facts and runtime data.

See [PRIVACY.md](PRIVACY.md), [TERMS.md](TERMS.md), and [SUPPORT.md](SUPPORT.md).

Dry-run one complete half-Block release:

```powershell
python scripts/fitness_data_bridge_release.py --workspace <Fitness-workspace> --release <workspace-release.json>
```

Supplying `--write` is not enough by itself. Live publication also requires the target Mac and an exact, unexpired one-time authorization file described in [operations.md](skills/fitness-data-bridge/references/operations.md).
