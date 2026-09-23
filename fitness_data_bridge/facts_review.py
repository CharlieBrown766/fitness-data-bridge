"""Read-only review of retained weekly facts; never mutates app or session state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .facts_refresh import analyze_action, build_summary, classify_record, facts_hash, file_sha256, parse_date, WeekRange
from .reconciliation import reconcile, deleted
from .layout import resolve_workspace, resolve_workspace_path
from .session_bridge import load_action_capabilities, _state_scalar


def review_payload(payload, capabilities):
    records = []
    for original in payload["records"]:
        record = dict(original)
        actions = []
        for action in original["actions"]:
            # Retained facts use simplified field names; recover source shape
            # explicitly, without copying earlier derived counts as raw data.
            raw = {k: action.get(k) for k in ("key", "label", "type", "exetype", "difficulty", "note")}
            raw["singleSide"] = action.get("single_side", False)
            raw["sets"] = [{
                "done": s["done"], "setType": s.get("set_type"),
                "weight": s.get("weight"), "left_weight": s.get("left_weight"),
                "reps": s.get("reps"), "time": s.get("time"),
                "unit": s.get("unit"), "selfWeight": s.get("self_weight"),
                "dropset": s.get("dropset"),
            } for s in action["sets"]]
            actions.append(analyze_action(raw, record["is_completed"], capabilities))
        record["actions"] = actions
        record["is_rest"], record["is_cardio"], record["is_strength"] = classify_record(
            record["title"], actions, record["is_completed"])
        for target, source in (("sets", "counted_sets"), ("side_sets", "counted_side_sets"),
                               ("reps", "counted_reps"), ("side_reps", "counted_side_reps"),
                               ("tonnage", "counted_tonnage"), ("heat_sets", "heat_sets")):
            record[target] = sum(a[source] for a in actions)
        record["parts"] = {}
        for a in actions:
            part = a.get("type") or "未标注"
            record["parts"][part] = record["parts"].get(part, 0) + a["counted_sets"]
        records.append(record)
    week = WeekRange(parse_date(payload["week"]["start"]), parse_date(payload["week"]["end"]))
    summary = build_summary(records, week)
    for surface in ("experience_feedback", "action_feedback"):
        for item in summary[surface]:
            item["feedback_id"] = facts_hash({"surface": surface, "record_id": item["record_id"],
                "action_key": item.get("action_key"), "action_index": item.get("action_index"), "text": item["text"]})
            item["disposition"] = "unreviewed"
    return {"source_sync_eligible": payload.get("planning_eligible", False),
            "summary": summary, "records": records}


def apply_feedback_dispositions(reviewed, events):
    """Overlay exact-source decisions; changed snapshots require fresh review."""
    for week in reviewed:
        for surface in ("experience_feedback", "action_feedback"):
            for item in week["summary"][surface]:
                matches = [event for event in events
                           if event.get("effect", {}).get("feedback_id") == item["feedback_id"]
                           and event.get("effect", {}).get("source_sha256") == week["source_sha256"]]
                if len(matches) == 1:
                    event = matches[0]
                    item.update(disposition=event["status"], correction_event_id=event["event_id"],
                                decision=event["effect"].get("decision"))
                elif matches:
                    item["disposition"] = "conflicting_decisions_require_review"


def session_candidates(sessions, records):
    result = []
    for session in sessions:
        if session["schedule"]["state"] in {"completed", "skipped", "cancelled"}:
            continue
        matches = [r for r in records if not deleted(r) and r["title"] == session["prescription"]["title"] and r["is_completed"]]
        if matches:
            result.append({"session_id": session["session_id"], "current_state": session["schedule"]["state"],
                "scheduled_for": session["schedule"].get("scheduled_for"),
                "status": "requires_receipt_identity_review" if len(matches) == 1 else "ambiguous",
                "records": [{"id": r["id"], "actual_date": r["datestr"], "sync_type": r["sync_type"]} for r in matches]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--facts", nargs="+", required=True)
    parser.add_argument("--receipts", nargs="*", default=[])
    parser.add_argument("--confirmed-links", help="Workspace JSON array of traceable user-confirmed session/record links")
    args = parser.parse_args()
    layout = resolve_workspace(args.workspace)
    capabilities = load_action_capabilities(layout)
    reviewed, seen = [], set()
    for value in args.facts:
        path = resolve_workspace_path(layout.root, value, field="facts")
        payload = json.loads(path.read_text(encoding="utf-8"))
        week = payload["week"]["key"]
        if week in seen:
            raise ValueError("Supply only one source snapshot per week")
        seen.add(week)
        item = review_payload(payload, capabilities)
        item.update(source_path=str(path.relative_to(layout.root)), source_sha256=file_sha256(path))
        reviewed.append(item)
    events_value = _state_scalar(layout.state_file, "correction_events_path")
    if events_value:
        events_path = resolve_workspace_path(layout.root, events_value, field="correction_events")
        apply_feedback_dispositions(reviewed, json.loads(events_path.read_text(encoding="utf-8")))
    index = resolve_workspace_path(layout.root, _state_scalar(layout.state_file, "session_index_path"), field="session_index")
    sessions = json.loads(index.read_text(encoding="utf-8"))
    receipts = [json.loads(resolve_workspace_path(layout.root, value, field="receipt").read_text(encoding="utf-8")) for value in args.receipts]
    confirmed = json.loads(resolve_workspace_path(layout.root, args.confirmed_links, field="confirmed_links").read_text(encoding="utf-8")) if args.confirmed_links else []
    for link in confirmed:
        source = resolve_workspace_path(layout.root, link.get("source_path"), field="confirmed_link.source_path")
        if file_sha256(source) != link.get("source_sha256"):
            raise ValueError("User confirmation source hash differs")
        source_text = json.dumps(json.loads(source.read_text(encoding="utf-8")), ensure_ascii=False)
        if not link.get("original_text") or json.dumps(link["original_text"], ensure_ascii=False) not in source_text:
            raise ValueError("User confirmation text is absent from its source")
    reconciliation = reconcile(sessions, [r for w in reviewed for r in w["records"]], receipts, confirmed)
    output = {"review_schema_version": 2, "reconciliation": reconciliation, "capabilities_sha256": facts_hash(capabilities), "weeks": reviewed,
              "session_candidates": session_candidates(sessions, [r for w in reviewed for r in w["records"]])}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
