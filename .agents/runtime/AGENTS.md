> Created time: 2026-08-16 09:48
> Modified time: 2026-08-19 22:47

# Fitness Data Bridge runtime contract

- This installed plugin owns only the Xunji, SynFit, Apple Calendar and Apple Health adapters.
- Read Fitness v5 sessions and personal-data bindings from the selected Fitness workspace; never choose or revise training content.
- Keep credentials, caches, records, health exports, backups, receipts and database files in workspace-owned private paths.
- External writes default to dry-run. Live plan publication requires one confirmed hash-valid half-Block release, one authorization bound to the whole revision, target-Mac execution, one backup-before, one Xunji transaction, one SynFit launch, one Calendar batch and one unified readback/receipt. Do not publish and verify its sessions one by one.
- Read connector configuration from `用户/数据接入/`, active machine state
  from `状态/`, formal records from `数据/`, Planner inputs from `计划/`, and
  keep credentials, caches, backups, logs and receipts under `运行/`. Use old
  directory names only to discover an unmigrated workspace.
