> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 00:51

# Fitness Connector development contract

- This repository owns application adapters only: Xunji, SynFit, Apple Calendar and Apple Health.
- It consumes Fitness v5 sessions and workspace personal-data bindings; it does not choose training content or duplicate generic Fitness rules.
- Credentials, caches, records, health exports, backups, receipts and database files stay in the Fitness workspace and must never enter this plugin.
- All external writes default to dry-run. Live publication is limited to the next scheduled, hash-valid session and requires a matching one-time authorization, target-Mac execution, backup, synchronization and readback.
- Do not install, publish, release or perform a live external write without the user's explicit request.
- Markdown files preserve `Created time`; update `Modified time` in Asia/Shanghai.
