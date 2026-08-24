---
name: fitness-data-bridge
description: Use when reading or refreshing Xunji and Apple Health data, previewing or publishing one governed Fitness v5.1 half-Block release, or repairing its Apple Calendar notes and reminders as an exact authorized batch.
---

> Created time: 2026-08-16 00:51
> Modified time: 2026-08-19 22:47

# Fitness Data Bridge

1. Read the target Fitness workspace `AGENTS.md`, `状态/state.yml`, and `用户/数据接入/connectors.json` before selecting a route.
2. Use [operations.md](references/operations.md) to choose exactly one adapter operation.
3. Treat the Fitness Planner half-Block release, its sessions and personal-data bindings as inputs. Never choose exercises, dose, loads, dates, progression, or lifecycle transitions.
4. Keep credentials, cache, facts, backups, receipts and external-source provenance in the workspace paths declared by the connector registry.
5. Default every external mutation to dry-run. Live plan publication accepts one confirmed v5.1 `first_half` or `second_half` release revision. Bind authorization to its release hash, ordered session IDs and replacement window; then perform one backup-before, one Xunji transaction for all strength sessions, one SynFit launch, one Calendar batch, and one unified database/Calendar readback and receipt. Never publish or verify the contained sessions one by one. The single-session command is compatibility-only.
6. Calendar repair is release-scoped and may replace only the projected event notes and alarms. It must bind a fresh one-time authorization to the exact release and projection, preserve event identity, start, end and calendar, update the batch once through EventKit, set and verify the per-event default-alert suppression state, and verify every note and alarm timestamp.
7. Do not alter completed Xunji rows, infer an action mapping absent from personal action capabilities, reuse an authorization, or silently continue after partial cross-system failure.
