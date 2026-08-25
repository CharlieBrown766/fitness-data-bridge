---
name: plugin-release
description: Build and validate a Fitness Data Bridge component Release for inclusion in Shujian Agent. Do not use it for composite delivery or installation into the developer Codex.
---

> Created time: 2026-08-21 10:45
> Modified time: 2026-08-25 02:34

# Fitness Data Bridge component Release

Build only from this authoritative source with `tools/build-public-package.py`.
Validate the adapter-only payload, privacy scan, checksums, archive and the
`shujian-component-release` attestation. The component package must name
`shujian-agent` as `marketplaceOwner`, set `marketplaceGenerated=false`, and
must not contain `.agents/plugins/marketplace.json` or an install entrypoint.

Do not generate, register, or install a component marketplace in any Codex home.
Only Shujian Agent may accept the validated component Release and issue the
marketplace plus `COMPONENTS.json`. Delivery, public composite publication,
and current-Codex updates route to
`<AgentRoot>/development/shujian-agent/.agents/skills/release-delivery/SKILL.md`.
