> Created time: 2026-08-16 09:48
> Modified time: 2026-08-16 10:51

# Fitness Connector 0.2.0

- Added the Fitness v5.1 half-Block release bridge and a dedicated preview/publish command.
- Bound one-time authorization to the entire release revision, ordered session list and replacement window.
- Reused the existing multi-record database and Calendar engines to perform one Xunji transaction, one SynFit launch, one Calendar batch and one unified readback/receipt.
- Added defense-in-depth checks against silent missing comparable kilogram loads.
- Retained the v0.1 single-session route for compatibility.

## 0.1.0

- Added bounded adapters for Xunji, SynFit, Apple Calendar and Apple Health.
- Kept credentials, caches, records, health exports and write receipts in the workspace-owned private database boundary.
- Added the Fitness v5 next-session bridge with hash validation, dry-run default and one-time write authorization.
- Added deterministic public packaging, privacy scanning, marketplace metadata and an installable release layout.
- Reverified the OpenAI plugin and Skill packaging contract on 2026-08-16.
