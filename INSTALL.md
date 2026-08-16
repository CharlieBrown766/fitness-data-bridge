> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 09:48

# Installation

Install from the public release package, not from the development source. The release contains a dedicated `fitness-connector` marketplace, checksum manifest and privacy-scanned runtime payload.

Runtime requirements are Python 3.11 or newer and `tzdata` on Windows. Live Xunji SQLite, SynFit and Apple Calendar publication must run on the target Mac; Windows supports workspace validation, API operations, Apple Health parsing and projection dry-runs.

Set `FITNESS_WORKSPACE` or pass `--workspace` explicitly. Credentials remain in the workspace private runtime path and are never placed in the plugin directory.

Build and verify the release:

```powershell
python tools\build-public-package.py
```

After extracting `fitness-connector-0.1.0-public.zip`, install its marketplace and plugin:

```powershell
codex plugin marketplace add <extracted-release-root> --json
codex plugin add fitness-connector@fitness-connector --json
codex plugin list --marketplace fitness-connector --json
```

Install Fitness Agent 1.0.0 first. Start a new Codex task after installation because existing tasks retain the plugin snapshot loaded at task start.
