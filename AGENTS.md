> Created time: 2026-08-16 00:51
> Modified time: 2026-08-25 02:21

# Fitness Data Bridge development contract

- This repository owns application adapters only: Xunji, SynFit, Apple Calendar and Apple Health.
- It consumes Fitness v5 sessions and workspace personal-data bindings; it does not choose training content or duplicate generic Fitness rules.
- Credentials, caches, records, health exports, backups, receipts and database files stay in the Fitness workspace and must never enter this plugin.
- All external writes default to dry-run. Live plan publication accepts one confirmed, hash-valid half-Block release revision containing all scheduled A1+B1 or A2+B2 sessions. It requires one matching authorization, target-Mac execution, one backup-before, one Xunji transaction, one SynFit launch, one Calendar batch and one unified readback/receipt. The legacy single-session route remains compatibility-only.
- Do not install, publish, release or perform a live external write without the user's explicit request.
- Markdown files preserve `Created time`; update `Modified time` in Asia/Shanghai.
- The canonical Fitness workspace layout is `用户/数据接入/` for connector
  configuration, `数据/` for formal imported records, `状态/` for active
  machine state, `计划/` for Planner artifacts, and `运行/` for credentials,
  caches, backups, logs and receipts. Previous directory names are discovery-only
  compatibility and must not be emitted by new writes.
- A component Release may build and validate Fitness Data Bridge as a Shujian
  Agent input, but must not install it into the developer's real `CODEX_HOME`.
  “Delivery”, “交付”, or a request to update the current Codex routes to
  `<AgentRoot>/development/shujian-agent/.agents/skills/release-delivery/SKILL.md`.
  Its standalone installation instructions are restricted to an isolated
  temporary `CODEX_HOME` and must not create a `fitness-data-bridge`
  marketplace in the developer environment.
