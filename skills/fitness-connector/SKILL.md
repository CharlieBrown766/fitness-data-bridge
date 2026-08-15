---
name: fitness-connector
description: Use when reading or refreshing Xunji and Apple Health data, previewing a governed Fitness v5 session for Xunji/SynFit/Apple Calendar, or performing an explicitly authorized next-session publication on the target Mac.
---

> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 00:51

# Fitness Connector

1. Read the target Fitness workspace `AGENTS.md`, `当前/state.yml`, and `个人/数据源/connectors.json` before selecting a route.
2. Use [operations.md](references/operations.md) to choose exactly one adapter operation.
3. Treat the Fitness Agent session and personal-data bindings as inputs. Never choose exercises, dose, dates, progression, or lifecycle transitions.
4. Keep credentials, cache, facts, backups, receipts and external-source provenance in the workspace paths declared by the connector registry.
5. Default every external mutation to dry-run. A live session publication is allowed only for the next scheduled v5 session, on the target Mac, with exact one-time authorization, backup-before, SynFit synchronization, independent database and Calendar readback, and a receipt.
6. Do not alter completed Xunji rows, infer an action mapping absent from personal action capabilities, reuse an authorization, or silently continue after partial cross-system failure.
