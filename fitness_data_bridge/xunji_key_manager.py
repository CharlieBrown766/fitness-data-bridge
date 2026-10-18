"""Local key import and explicit uncached read verification; never issues keys."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import time

from .io import atomic_write_json
from .layout import default_open_api_cache_dir
from .xunji_credentials import (
    CredentialError, VerifiedReadCredential, credential_account_scope, credential_status,
    import_credential, open_token_digest, read_credentials_document,
    resolve_credentials_file, stage_open_credential, validate_open_token,
)
from .xunji_open_api import (
    TRAINING_QUERY, TRAINING_QUERY_FULL, PLAN_QUERY,
    _enforce_local_interval, _write_limiter_marker,
)
from .xunji_client import validate_datestr
from .xunji_transport import XunjiOpenApiError, post_json, redact_secrets

READ_PROBES = {e.name: e for e in (TRAINING_QUERY, TRAINING_QUERY_FULL, PLAN_QUERY)}


def verify_open_read(path: str | Path, endpoint_name: str, *, datestr: str | None = None,
                     activate_read: bool = False, cache_dir: str | Path,
                     timeout: float = 30.0, clock=time.time) -> dict:
    """Verify only the chosen registered read; success grants no write permission."""
    if endpoint_name not in READ_PROBES:
        raise CredentialError("Only registered training/official-plan read probes are supported")
    endpoint = READ_PROBES[endpoint_name]
    target = Path(path).expanduser().resolve()
    before = target.read_bytes()
    document = read_credentials_document(target)
    candidate = document.get("open_api")
    if document["schema_version"] != 2 or not isinstance(candidate, dict):
        raise CredentialError("Stage an Open API key before read verification")
    token = validate_open_token(candidate.get("key"))
    if endpoint_name == "plan.query":
        body = {"schema_version": "plan_open_api_v1", "action": "list"}
        field = "plans"
    else:
        if datestr is None:
            raise CredentialError("Training read verification requires an explicit date")
        body = {"schema_version": "train_open_api_v2", "datestr": validate_datestr(datestr),
                "include_full_data": endpoint_name == "training.query_full"}
        field = "trains"
    # A temporary endpoint-bound token authorizes this one explicitly requested probe.
    credential = VerifiedReadCredential(token, slot=endpoint.credential_slot, allowed_urls={endpoint.url})
    scope = credential_account_scope(endpoint.credential_slot, credential)
    directory = Path(cache_dir).expanduser().resolve()
    marker = _enforce_local_interval(directory, endpoint, account_scope=scope, clock=clock)
    requested = clock()
    _write_limiter_marker(marker, endpoint, epoch=requested)
    payload = post_json(url=endpoint.url, body=body, credential=credential, timeout=timeout,
                        success_required=endpoint.success_required).payload
    res = payload.get("res")
    if not isinstance(res, dict) or not isinstance(res.get(field), list):
        raise XunjiOpenApiError("Read probe did not return the expected data shape; key was not activated")
    if target.read_bytes() != before:
        raise CredentialError("Credential file changed during verification; result was not activated")
    at = datetime.fromtimestamp(requested, timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    candidate.setdefault("verified_reads", {})[endpoint_name] = {
        "credential_sha256": open_token_digest(token), "verified_at": at,
    }
    if activate_read:
        document.setdefault("read_bindings", {})[endpoint.credential_slot] = "open_api"
    atomic_write_json(target, document)
    return {"endpoint": endpoint_name, "response_source": "live_api", "verified_at": at,
            "activated_read": activate_read, "write_permission_verified": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace")
    parser.add_argument("--credentials-file", type=Path)
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("import", "stage-open"):
        command = sub.add_parser(name)
        command.add_argument("--key-file", type=Path, help="Read token from this file; otherwise stdin")
        command.add_argument("--replace", action="store_true")
        if name == "import":
            command.add_argument("--slot", choices=("training", "food_records", "food_search", "body"), required=True)
    sub.add_parser("status")
    verify = sub.add_parser("verify-open")
    verify.add_argument("--endpoint", choices=tuple(READ_PROBES), required=True)
    verify.add_argument("--date")
    verify.add_argument("--activate-read", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = resolve_credentials_file(args.credentials_file, workspace=args.workspace)
        if args.operation in ("import", "stage-open"):
            token = args.key_file.read_text(encoding="utf-8-sig").strip() if args.key_file else sys.stdin.read().strip()
            if args.operation == "import":
                import_credential(path, args.slot, token, replace=args.replace)
            else:
                stage_open_credential(path, token, replace=args.replace)
            result = credential_status(path)
        elif args.operation == "status":
            result = credential_status(path)
        else:
            result = verify_open_read(path, args.endpoint, datestr=args.date,
                activate_read=args.activate_read, cache_dir=default_open_api_cache_dir(args.workspace))
    except (CredentialError, XunjiOpenApiError, OSError, ValueError) as exc:
        print(json.dumps({"error": redact_secrets(exc, (locals().get("token", ""),))}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
