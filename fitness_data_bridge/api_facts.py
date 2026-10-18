"""Convert retained full Open API reads to review facts with bound identities."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path

from .errors import ValidationError
from .facts_refresh import WeekRange, analyze_action, build_summary, classify_record, facts_hash, note_text, parse_date
from .layout import resolve_workspace, resolve_workspace_path
from .session_bridge import _state_scalar, _workspace_file
from .xunji_client import SCHEMA_VERSION, TRAINS_PATH, validate_datestr
from .xunji_open_api import TRAINING_QUERY_FULL, request_digest


def _bound_json(data: bytes, digest: str):
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValidationError("Identity-owner SHA-256 differs from its binding")
    return json.loads(data)


def _identity_index(registry):
    result = {}
    for key, label in registry["by_key"].items():
        for name in [label] + registry.get("aliases", {}).get(key, []) + registry.get("ambiguous_labels", {}).get(key, []):
            result.setdefault(name, set()).add(key)
    return result


def retained_full_response(envelope: object) -> dict:
    """Require retained request evidence; a bare response cannot attest full mode."""
    if not isinstance(envelope, dict):
        raise ValidationError("Expected a retained full-training cache envelope")
    if envelope.get("endpoint") == TRAINS_PATH:
        request = envelope
        if envelope.get("schema_version") != SCHEMA_VERSION:
            raise ValidationError("Unsupported retained training schema")
    elif envelope.get("endpoint") == TRAINING_QUERY_FULL.name:
        request = envelope.get("request")
        if (envelope.get("schema_version") != 1 or not isinstance(request, dict)
                or envelope.get("request_digest") != request_digest(TRAINING_QUERY_FULL, request)):
            raise ValidationError("Retained full-training request digest differs")
        if request.get("schema_version") != SCHEMA_VERSION:
            raise ValidationError("Unsupported retained training request schema")
    else:
        raise ValidationError("Expected a registered full-training cache envelope, not a bare response")
    if request.get("include_full_data") is not True:
        raise ValidationError("Retained training request must use include_full_data=true")
    validate_datestr(request.get("datestr", ""))
    response = envelope.get("response")
    if (not isinstance(response, dict) or response.get("success") is False
            or not isinstance(response.get("res"), dict)
            or not isinstance(response["res"].get("trains"), list)):
        raise ValidationError("Retained full-training response must contain res.trains")
    return response


def normalize_api_week(payload: dict, *, week_start: str, registry_data: bytes,
                       registry_sha256: str, capabilities_data: bytes,
                       capabilities_sha256: str) -> dict:
    """Pure conversion; API provenance never impersonates settled Mac facts."""
    registry = _bound_json(registry_data, registry_sha256)
    capabilities = _bound_json(capabilities_data, capabilities_sha256)
    if not isinstance(registry.get("by_key"), dict) or not isinstance(capabilities.get("actions"), list):
        raise ValidationError("Missing identity/capability owner contents")
    caps = {a["key"]: a for a in capabilities["actions"]}
    index = _identity_index(registry)
    start = parse_date(week_start)
    if start.weekday() != 0:
        raise ValidationError("API facts week must begin on Monday")
    week = WeekRange(start, start + timedelta(days=6))
    trains = payload.get("res", {}).get("trains") if isinstance(payload.get("res"), dict) else None
    if not isinstance(trains, list):
        raise ValidationError("Full API response must contain res.trains")
    records = []

    def action_view(raw, completed):
        source = deepcopy(raw)
        issues = []
        name = source.get("name", "")
        matches = index.get(name, set())
        key = next(iter(matches)) if len(matches) == 1 else None
        if key in registry.get("ambiguous_labels", {}):
            issues.append({"reason": "ambiguous_api_action_key", "name": name, "key": key})
            key = None
        cap = caps.get(key)
        if not key and not source.get("cardio"):
            issues.append({"reason": "unresolved_api_action_name", "name": name})
        canonical = registry["by_key"].get(key, name)
        # A personal historic label is legal only when it resolves to the same key.
        label = cap["label"] if cap and key in index.get(cap["label"], set()) else canonical
        original_sets = source.get("sets", [])
        if not isinstance(original_sets, list):
            original_sets = []
            issues.append({"reason": "unsupported_api_sets_shape"})
        converted = []
        side_known = isinstance(source.get("singleSide"), bool)
        for number, original in enumerate(original_sets):
            if not isinstance(original, dict):
                issues.append({"reason": "unsupported_api_set", "set_index": number})
                continue
            item = deepcopy(original)
            if "done" not in item:
                item["done"] = False
                issues.append({"reason": "missing_api_set_completion", "set_index": number})
            if "weight" not in item and "weight_kg" in item:
                item["weight"], item["unit"] = item["weight_kg"], "kg"
            if "time" not in item and "duration_s" in item:
                item["time"] = item["duration_s"]
            if "items" in item:
                issues.append({"reason": "unsupported_nested_superset", "set_index": number})
            if "dropSets" in item:
                drops = item.pop("dropSets")
                if "dropset" in item:
                    issues.append({"reason": "conflicting_drop_shapes", "set_index": number})
                if not isinstance(drops, list):
                    issues.append({"reason": "unsupported_api_drop_sets", "set_index": number})
                else:
                    native = []
                    for drop in drops:
                        segment = drop.get("set", drop) if isinstance(drop, dict) else None
                        if not isinstance(segment, dict) or any(k in segment for k in ("items", "dropSets", "dropset")):
                            issues.append({"reason": "unsupported_nested_drop", "set_index": number})
                            continue
                        segment = deepcopy(segment)
                        if isinstance(drop, dict) and "done" in drop:
                            segment.setdefault("done", drop["done"])
                        native.append(segment)
                    item["dropset"] = native
            converted.append(item)
        ambiguous_sides = not side_known and any(
            s.get("left_weight", s.get("leftWeight")) not in (None, "") for s in converted)
        if ambiguous_sides:
            issues.append({"reason": "api_laterality_not_explicit"})
        native = {**source, "key": key, "label": label, "sets": converted,
                  "singleSide": source.get("singleSide", False), "source_issues": issues}
        if ambiguous_sides:
            native["exetype"] = "unresolved_laterality"
        if source.get("cardio"):
            native["exetype"] = "cardio"
        return analyze_action(native, completed, caps)

    for train in trains:
        if not isinstance(train, dict):
            raise ValidationError("API train must be an object")
        day = parse_date(train.get("datestr", ""))
        if not week.start <= day <= week.end:
            continue
        movements = train.get("movements", [])
        if not isinstance(movements, list) or any(not isinstance(m, dict) for m in movements):
            raise ValidationError("API movements must be objects")
        try:
            completed = int(train.get("start") or 0) > 0 and int(train.get("end") or 0) > 0
        except (ValueError, TypeError):
            completed = False
        actions = []
        for movement_index, movement in enumerate(movements):
            sets = movement.get("sets", [])
            has_superset = isinstance(sets, list) and any(isinstance(s, dict) and "items" in s for s in sets)
            if not has_superset:
                positioned = {**movement, "_source_position": {"movement_index": movement_index}}
                actions.append(action_view(positioned, completed))
                continue
            # Keep the original wrapper in source_actions, and each child's round position in its view.
            children = {}
            for round_index, round_set in enumerate(sets):
                items = round_set.get("items") if isinstance(round_set, dict) else None
                if not isinstance(items, list) or not items:
                    fallback = deepcopy(movement)
                    fallback["sets"] = [round_set]
                    actions.append(action_view(fallback, completed))
                    continue
                for child_index, child in enumerate(items):
                    if not isinstance(child, dict) or not isinstance(child.get("set"), dict):
                        raise ValidationError("Superset child must contain a set object")
                    identity = (child_index, child.get("name"))
                    target = children.setdefault(identity, {**{k: deepcopy(v) for k, v in child.items() if k != "set"},
                        "_source_position": {"movement_index": movement_index, "child_index": child_index}, "sets": []})
                    item = deepcopy(child["set"])
                    item.setdefault("done", child.get("done", round_set.get("done", False)))
                    if "setType" in round_set:
                        item.setdefault("setType", round_set["setType"])
                    item["_source_location"] = {"movement_index": movement_index, "round_index": round_index, "child_index": child_index}
                    target["sets"].append(item)
            actions.extend(action_view(child, completed) for child in children.values())
        title = train.get("title", "")
        is_rest, is_cardio, is_strength = classify_record(title, actions, completed)
        is_cardio = is_cardio or any(m.get("cardio") is True for m in movements)
        raw_note = train.get("note")
        try:
            metadata = json.loads(raw_note) if isinstance(raw_note, str) else raw_note
        except ValueError:
            metadata = None
        record = {"id": train.get("localid"), "version": train.get("version"), "datestr": day.isoformat(),
            "title": title, "start": train.get("start"), "end": train.get("end"), "delflag": 0,
            "is_completed": completed, "is_rest": is_rest, "is_cardio": is_cardio, "is_strength": is_strength,
            "sync_type": "api_read", "source_content_sha256": facts_hash(train),
            "source_actions": deepcopy(movements), "source_note": deepcopy(raw_note),
            "heart_rate": deepcopy(train.get("heartRate", metadata.get("heartRate") if isinstance(metadata, dict) else None)),
            "note_text": note_text(raw_note), "experience_text": note_text(raw_note), "actions": actions, "parts": {}}
        for target, field in (("sets", "counted_sets"), ("side_sets", "counted_side_sets"),
                              ("reps", "counted_reps"), ("side_reps", "counted_side_reps"),
                              ("tonnage", "counted_tonnage"), ("heat_sets", "heat_sets")):
            record[target] = sum(a[field] for a in actions)
        records.append(record)
    summary = build_summary(records, week)
    return {"facts_schema_version": 5, "source_contract": "train_open_api_v2_full",
        "source_sha256": facts_hash(payload), "registry_sha256": registry_sha256,
        "normalization_capabilities_sha256": capabilities_sha256,
        "planning_eligible": False, "planning_eligibility_reason": "API review evidence has no post-SynFit settle attestation",
        "statistics_complete": summary["statistics_complete"],
        "week": {"start": week.start.isoformat(), "end": week.end.isoformat(), "key": week.key},
        "records": records, "summary": summary}


def load_selected_identity_owners(layout, planner_root: Path):
    """Resolve exact state-bound plugin bytes, never a caller-substituted registry."""
    resource_root = (planner_root / "resources").resolve()
    def resource(uri):
        prefix = "plugin://fitness-planner/"
        if not isinstance(uri, str) or not uri.startswith(prefix):
            raise ValidationError("Expected canonical Planner resource locator")
        relative = uri[len(prefix):]
        if "\\" in relative or any(x in relative.split("/") for x in ("", ".", "..")) or any(x in relative for x in ("?", "#", "%")):
            raise ValidationError("Invalid Planner resource locator")
        target = (resource_root / relative).resolve()
        if not target.is_relative_to(resource_root):
            raise ValidationError("Planner resource escapes its root")
        return target.read_bytes()
    bundle = _bound_json(resource(_state_scalar(layout.state_file, "reference_bundle_path")),
                         _state_scalar(layout.state_file, "reference_bundle_sha256"))
    registry = bundle["owners"]["official_action_registry"]
    index = json.loads(_workspace_file(layout.root, _state_scalar(layout.state_file, "personal_data_path"), field="personal_data_path").read_text(encoding="utf-8"))
    cap = index["action_capabilities"]
    return {"registry_data": resource(registry["path"]), "registry_sha256": registry["sha256"],
            "capabilities_data": _workspace_file(layout.root, cap["path"], field="action_capabilities").read_bytes(),
            "capabilities_sha256": cap["sha256"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--planner-root", type=Path, required=True)
    parser.add_argument("--api-file", required=True, help="Retained cache envelope with include_full_data=true request")
    parser.add_argument("--week-start", required=True)
    parser.add_argument("--output", required=True, help="New workspace-relative review-facts file")
    args = parser.parse_args(argv)
    layout = resolve_workspace(args.workspace)
    source = resolve_workspace_path(layout.root, args.api_file, field="api_file")
    output = resolve_workspace_path(layout.root, args.output, field="output")
    source_data = source.read_bytes()
    response = retained_full_response(json.loads(source_data))
    payload = normalize_api_week(response, week_start=args.week_start,
                                 **load_selected_identity_owners(layout, args.planner_root))
    payload["source_path"] = str(source.relative_to(layout.root))
    payload["source_file_sha256"] = hashlib.sha256(source_data).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"records": len(payload["records"]), "statistics_complete": payload["statistics_complete"],
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
