---
name: plugin-release
description: Build and validate a Fitness Data Bridge component Release for inclusion in Shujian Agent. Do not use it for composite delivery or installation into the developer Codex.
---

> Created time: 2026-08-21 10:45
> Modified time: 2026-08-21 10:45

# Fitness Data Bridge component Release

Build only from this authoritative source with `tools/build-public-package.py`.
Validate the adapter-only public payload, privacy scan, checksums, archive, and
installation contract in an isolated temporary `CODEX_HOME`.

Do not install this component into the developer's real Codex or create a
`fitness-data-bridge` marketplace there. Delivery, public composite publication,
and current-Codex updates route to
`<AgentRoot>/development/shujian-agent/.agents/skills/release-delivery/SKILL.md`.
