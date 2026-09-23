---
name: fitness-data-bridge
description: Use when reading or refreshing Xunji and Apple Health data, previewing or publishing one governed Fitness v5.1 half-Block release, or repairing its Apple Calendar notes and reminders as an exact authorized batch.
---

> Created time: 2026-08-16 00:51
> Modified time: 2026-09-24 00:49

# Fitness Data Bridge

1. Read the target Fitness workspace `AGENTS.md`, `状态/state.yml`, and `用户/数据接入/connectors.json` before selecting a route.
2. Use [operations.md](references/operations.md) to choose exactly one adapter operation.
3. Treat the Fitness Planner half-Block release, its sessions and personal-data bindings as inputs. Never choose exercises, dose, loads, dates, progression, or lifecycle transitions.
4. Keep credentials, cache, facts, backups, receipts and external-source provenance in the workspace paths declared by the connector registry.
5. Default every external mutation to dry-run. Live plan publication accepts one confirmed v5.1 `first_half` or `second_half` release revision. Bind authorization to its release hash, ordered session IDs and replacement window; then perform one backup-before, one Xunji transaction for all strength sessions, one SynFit launch, one Calendar batch, and one unified database/Calendar readback and receipt. Never publish or verify the contained sessions one by one. The single-session command is compatibility-only.
6. Calendar repair is release-scoped and may replace only the projected event notes and alarms. It must bind a fresh one-time authorization to the exact release and projection, preserve event identity, start, end and calendar, update the batch once through EventKit, set and verify the per-event default-alert suppression state, and verify every note and alarm timestamp.
7. Do not alter completed Xunji rows, infer an action mapping absent from personal action capabilities, reuse an authorization, or silently continue after partial cross-system failure. A later release revision retains completed sessions in the governed release identity but projects only its scheduled sessions into the replacement batch.
8. Project `paired_left_right_per_set` as one Mac-local Xunji set row containing `weight` and `left_weight`, with movement-level `singleSide: true`. When refreshing facts, also accept the API/watch transport alias `leftWeight`; expose both raw record-set counts and normalized side-set counts. Keep an explicit session-to-Xunji date/title identity map in the projection and publication receipt.
9. Xunji training experience and action notes are user-feedback surfaces. Keep the training experience empty during publication and export only explicit natural-language `settings.user_facing_instruction` plus a necessary translated duration instruction to an action note. Never write provenance, intent labels, IDs, hashes, or internal markers into either field.
10. For a requested multi-week refresh, enumerate every natural-week start in the requested interval and refresh each exact week; do not treat `latest_summary.md` as proof that intervening weeks exist. Require the stable post-SynFit settle check before marking each week planning-eligible.
11. Return SHA-256 values for every generated week artifact. If the requesting device cannot yet see the Mac's OneDrive outputs and an authorized cross-device transport is used, compare source and destination hashes before accepting the copied files as the same facts; do not silently replace an existing different artifact.
12. Distinguish synchronization readiness from statistics completeness. Preserve raw record types; normalize absent loaded-action types only through hash-bound personal mappings. Report unclassified loaded sets explicitly, never as zero workload. Feedback must retain record identity, version and action position. A missing personal mapping routes to workspace data; modify the Connector only for a demonstrated read/projection defect with valid inputs.
13. Use `python -m fitness_data_bridge.facts_review --workspace <Fitness> --facts <week.json> [<week.json> ...]` to review retained facts without connecting to apps. It returns recalculated statistics, traceable feedback and session-match candidates, and never changes lifecycle. Save reviewed decisions in workspace correction events through Planner, and require receipt identity evidence before applying completion candidates.


## Xunji execution authority

Active Xunji plans are the current execution arrangements; completed records and traceable user corrections establish actual execution. Calendar is reminder-only and may be stale. Never infer missed training from a Calendar mismatch or overwrite Xunji using Calendar. Reminder writeback requires an explicit user request. Preserve original workspace prescriptions and terminal states.

Refresh all relevant natural weeks before reconciliation, including the original and moved dates. Facts schema 5 retains deleted rows and raw action content for lineage review, but excludes deleted rows from workload totals. Exact completed source duplicates are annotated and counted once; future copied plans are never deduplicated. Source records remain intact. The record window is explicit: absence from it does not prove cancellation.

Run `python -m fitness_data_bridge.facts_review --workspace <Fitness> --facts <week.json> [<week.json> ...] --receipts <publication-receipt.json> [<receipt.json> ...]`. Inspect `reconciliation`: receipt IDs link directly, copy/delete successors remain candidates until lineage or user confirmation resolves them. A workspace `--confirmed-links <links.json>` array may contain `session_id`, `record_id`, `original_text`, workspace-relative `source_path` and the SHA-256 of its retained user-correction source as `source_sha256`. This is evidence overlay, never authorization to mutate lifecycle. Review active unfinished arrangements before generating or replacing a release.

Drop segments contribute repetition and load totals without adding main set rows. Unknown nested shapes or missing paired-side semantics mark statistics incomplete. Missing personal mappings list the affected actions and sets; do not infer recording types or per-hand loads from an official key alone.

For an application identity audit, run `python -m fitness_data_bridge.action_export --bundle <main.jsbundle> --app-version <version> --output <new-workspace-candidate.json>`. Output is candidate-only, includes source hash and duplicate keys, and never replaces a Planner registry automatically. Structural extraction is not proof of UI behavior.

Use [Xunji compatibility](references/xunji-compatibility.md) for audited app identity, evidence surfaces and interface verification limits.
