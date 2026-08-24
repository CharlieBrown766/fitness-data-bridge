> Created time: 2026-08-16 00:51
> Modified time: 2026-08-25 02:18

# Installation

Install from the public release package, not from the development source. The release contains a dedicated `fitness-data-bridge` marketplace, checksum manifest and privacy-scanned runtime payload.

Runtime requirements are Python 3.11 or newer and `tzdata` on Windows. Live Xunji SQLite, SynFit and Apple Calendar publication must run on the target Mac; Windows supports workspace validation, API operations, Apple Health parsing and projection dry-runs.

Set `FITNESS_WORKSPACE` or pass `--workspace` explicitly. Credentials remain in the workspace private runtime path and are never placed in the plugin directory.

Build and verify the release:

```powershell
python tools\build-public-package.py
```

After extracting `fitness-data-bridge-1.0.5-public.zip`, install its marketplace and plugin:

```powershell
codex plugin marketplace add <extracted-release-root> --json
codex plugin add fitness-data-bridge@fitness-data-bridge --json
codex plugin list --marketplace fitness-data-bridge --json
```

Install Fitness Planner 2.0.4 first. Start a new Codex task after installation because existing tasks retain the plugin snapshot loaded at task start.

After the new plugin passes verification, remove the legacy
`fitness-connector@fitness-connector` registration and change the active
workspace `用户/数据接入/connectors.json` selection from `fitness-connector` to
`fitness-data-bridge`. Historical receipts remain unchanged; new receipts use
`运行/receipts/data-bridge/`.
