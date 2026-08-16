> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 12:00

# Fitness Data Bridge development contract

- This repository owns application adapters only: Xunji, SynFit, Apple Calendar and Apple Health.
- It consumes Fitness v5 sessions and workspace personal-data bindings; it does not choose training content or duplicate generic Fitness rules.
- Credentials, caches, records, health exports, backups, receipts and database files stay in the Fitness workspace and must never enter this plugin.
- All external writes default to dry-run. Live plan publication accepts one confirmed, hash-valid half-Block release revision containing all scheduled A1+B1 or A2+B2 sessions. It requires one matching authorization, target-Mac execution, one backup-before, one Xunji transaction, one SynFit launch, one Calendar batch and one unified readback/receipt. The legacy single-session route remains compatibility-only.
- Do not install, publish, release or perform a live external write without the user's explicit request.
- Markdown files preserve `Created time`; update `Modified time` in Asia/Shanghai.
