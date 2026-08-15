> Created time: 2026-08-16 00:51
> Modified time: 2026-08-16 00:51

# Installation

This source tree is not installed automatically. Validate it locally first, then install only after explicit user approval.

Runtime requirements are Python 3.11 or newer and `tzdata` on Windows. Live Xunji SQLite, SynFit and Apple Calendar publication must run on the target Mac; Windows supports workspace validation, API operations, Apple Health parsing and projection dry-runs.

Set `FITNESS_WORKSPACE` or pass `--workspace` explicitly. Credentials remain in the workspace private runtime path and are never placed in the plugin directory.
