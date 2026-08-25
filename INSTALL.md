> Created time: 2026-08-16 00:51
> Modified time: 2026-08-25 11:04

# Installation

Fitness Data Bridge is distributed only as a Shujian Agent component input. Its
release contains a privacy-scanned plugin payload, checksum manifest and a
compatibility attestation owned by `shujian-agent`; it contains no marketplace
manifest or installer.

Runtime requirements are Python 3.11 or newer and `tzdata` on Windows. Live Xunji SQLite, SynFit and Apple Calendar publication must run on the target Mac; Windows supports workspace validation, API operations, Apple Health parsing and projection dry-runs.

Set `FITNESS_WORKSPACE` or pass `--workspace` explicitly. Credentials remain in the workspace private runtime path and are never placed in the plugin directory.

Build and verify the release:

```powershell
python tools\build-public-package.py
```

After extracting the component ZIP, verify that it declares
`packageType=shujian-component-release`, `marketplaceOwner=shujian-agent` and
`marketplaceGenerated=false`. It is accepted and installed only through a
Shujian Agent composite Release:

```powershell
python <AgentRoot>\development\shujian-agent\tools\build-composite-package.py
```

Do not create or register a `fitness-data-bridge` marketplace. Shujian Agent
resolves and signs the Planner dependency, emits the only marketplace manifest,
and performs installation transactionally. Start a new Codex task after the
composite package is delivered.

After the new plugin passes verification, remove the legacy
`fitness-connector@fitness-connector` registration and change the active
workspace `用户/数据接入/connectors.json` selection from `fitness-connector` to
`fitness-data-bridge`. Historical receipts remain unchanged; new receipts use
`运行/receipts/data-bridge/`.
