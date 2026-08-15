> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 01:24

# Fitness Connector

This private plugin is the application boundary between the generic Fitness Agent and the workspace-owned personal Fitness database.

It provides:

- Xunji training, food and body-data clients, cache controls and credential slots;
- Xunji local SQLite publication with completed-record protection and exact readback;
- SynFit launch and bounded synchronization polling on the target Mac;
- Apple Calendar scoped write and independent readback;
- Apple Health export staging and parsing into workspace facts;
- a v5 session bridge that publishes only the next scheduled session.

The plugin contains no personal records or credentials. `fitness-agent` remains the owner of generic rules and validators; the Fitness workspace remains the owner of personal intent, capabilities, plans, facts and runtime data.

See [PRIVACY.md](PRIVACY.md), [TERMS.md](TERMS.md), and [SUPPORT.md](SUPPORT.md).

Dry-run the next session:

```powershell
python scripts/fitness_connector_session.py --workspace <Fitness-workspace>
```

Supplying `--write` is not enough by itself. Live publication also requires the target Mac and an exact, unexpired one-time authorization file described in [operations.md](skills/fitness-connector/references/operations.md).
