> Created time: 2026-08-16 00:51
> Modified time: 2026-10-30 19:34

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
- A component Release contains only the plugin payload, checksum manifest and
  compatibility attestation naming `shujian-agent` as marketplace owner. It
  must not contain or generate `.agents/plugins/marketplace.json`, an installer,
  or a marketplace registration.
  “Delivery”, “交付”, or a request to update the current Codex routes to
  `<AgentRoot>/development/shujian-agent/.agents/skills/release-delivery/SKILL.md`.
  Only Shujian Agent may accept this component into `COMPONENTS.json`, issue the
  marketplace manifest, and install it. Component tests validate payloads
  directly and never create a temporary component marketplace.

## Linear 工作记录

- 本组件绑定 Shujian Agent 工作区的 `Fitness Data Bridge · 数据接入与发布`（P-SHU-37）；精确 UUID、范围与任务链接见 `.agents/development/linear-project.json`。
- 使用独立 `linear-worklog` Skill：开始时恢复本项目 pending，关键阶段同步 issue，结束前读回核验；项目简报按实际成果发布。
- 本绑定只覆盖本组件开发与验收。个人业务数据、其他 Agent 和组合 Release/Delivery 按各自范围归属，不默认写入本项目。
