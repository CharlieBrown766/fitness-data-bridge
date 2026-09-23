"""Read-only Xunji execution evidence. Calendar reminders never decide identity."""
from __future__ import annotations


SOURCE_POLICY = {
    "current_arrangements": "xunji_active_records",
    "actual_execution": "xunji_completed_records_and_user_confirmation",
    "calendar": "reminder_only",
    "calendar_writeback": "explicit_request_required",
    "original_prescription": "immutable_comparison_baseline",
}


def deleted(record):
    return str(record.get("delflag", 0)) not in {"0", "None", "", "False"}


def duplicate_groups(records):
    """Only identical completed source rows with real timestamps qualify.

    Content-identical future copies are valid arrangements, not duplicates.
    Old facts lacking an exact source-content hash are not deduplicated.
    """
    buckets = {}
    for r in records:
        if deleted(r) or not r.get("is_completed") or not r.get("source_content_sha256"):
            continue
        try:
            start, end = int(r.get("start")), int(r.get("end"))
        except (TypeError, ValueError, OverflowError):
            continue
        if start <= 0 or end <= start:
            continue
        key = (r.get("datestr"), start, end, r.get("title"), r["source_content_sha256"])
        buckets.setdefault(key, []).append(r)
    return [{"retained_id": group[0]["id"], "source_ids": [r["id"] for r in group],
             "basis": "identical_completed_source_content_and_interval"}
            for group in buckets.values() if len(group) > 1]


def receipt_links(receipts):
    """Use successful DB readback identity, never assume insertion order."""
    links = {}
    for receipt in receipts:
        if receipt.get("status") != "succeeded":
            continue
        rows = receipt.get("database_readback", {}).get("rows", [])
        for item in receipt.get("database_identity_map", []):
            matches = [r for r in rows if r.get("date") == item.get("date")
                       and r.get("title") == item.get("title")]
            if len(matches) == 1:
                links.setdefault(item["session_id"], set()).add(str(matches[0]["id"]))
    return links


def action_keys(record):
    return [a.get("key") for a in record.get("actions", []) if a.get("key")]


def prescription_comparison(session, record):
    """Expose set changes without guessing side/load equivalence across models."""
    planned = session["prescription"].get("movements", [])
    original_keys = [a["action_key"] for a in planned]
    current_keys = action_keys(record)
    return {"added_action_keys": sorted(set(current_keys) - set(original_keys)),
            "removed_action_keys": sorted(set(original_keys) - set(current_keys)),
            "action_order_changed": original_keys != current_keys,
            "original_movements": planned,
            "current_actions": record.get("source_actions", record.get("actions", [])),
            "set_comparison_requires_recording_semantics": True}


def reconcile(sessions, records, receipts=(), confirmed_links=()):
    """Return evidence only. Copies require lineage or traceable confirmation.

    confirmed_links entries contain session_id, record_id, source_sha256,
    original_text; they must be prepared from a workspace user correction.
    """
    links = receipt_links(receipts)
    active = [r for r in records if not deleted(r)]
    result = []
    claimed = {}
    for session in sessions:
        if session["schedule"]["state"] in {"completed", "skipped", "cancelled"}:
            continue
        sid = session["session_id"]
        original_ids = links.get(sid, set())
        originals = [r for r in records if str(r["id"]) in original_ids]
        confirmations = [x for x in confirmed_links if x.get("session_id") == sid
                         and x.get("original_text") and isinstance(x.get("source_sha256"), str)
                         and len(x["source_sha256"]) == 64]
        confirmed_ids = {str(x["record_id"]) for x in confirmations}
        candidates = []
        for r in active:
            same_id = str(r["id"]) in original_ids
            confirmed = str(r["id"]) in confirmed_ids
            same_title = r.get("title") == session["prescription"]["title"]
            content_overlap = max((len(set(action_keys(r)) & set(action_keys(o))) /
                                   max(1, len(set(action_keys(o)))) for o in originals), default=0)
            if not (same_id or confirmed or same_title or content_overlap >= 0.5):
                continue
            basis = "user_confirmation" if confirmed else "receipt_record_id" if same_id else "candidate_only"
            candidates.append({"record_id": r["id"], "current_date": r.get("datestr"),
                "is_completed": bool(r.get("is_completed")), "basis": basis,
                "title_matches": same_title, "action_overlap": content_overlap,
                "prescription_comparison": prescription_comparison(session, r),
                "source_content_sha256": r.get("source_content_sha256"),
                "confirmation_sources": [{k: x.get(k) for k in
                    ("source_path", "source_sha256", "original_text")}
                    for x in confirmations if str(x["record_id"]) == str(r["id"])],
                "date_changed": r.get("datestr") != session["schedule"].get("scheduled_for"),
                "content_changed": any(action_keys(r) != action_keys(o) or
                    r.get("source_content_sha256") != o.get("source_content_sha256") for o in originals
                    if str(o["id"]) != str(r["id"])),
                "sync_type": r.get("sync_type"), "record_version": r.get("version")})
        proven = [c for c in candidates if c["basis"] != "candidate_only"]
        status = "linked" if len(proven) == 1 else "ambiguous" if len(proven) > 1 else (
            "copy_or_extra_requires_confirmation" if candidates else
            "deleted_without_confirmed_successor" if originals and all(deleted(o) for o in originals) else
            "not_observed_in_supplied_window")
        item = {"session_id": sid, "original_date": session["schedule"].get("scheduled_for"),
                "status": status, "original_record_ids": sorted(original_ids), "candidates": candidates,
                "lifecycle_mutation": False, "calendar_mismatch_is_training_error": False}
        if status == "linked":
            item["selected_record_id"] = proven[0]["record_id"]
            claimed.setdefault(str(proven[0]["record_id"]), []).append(item)
        result.append(item)
    for matches in claimed.values():
        if len(matches) > 1:
            for item in matches:
                item["status"] = "ambiguous_record_claimed_by_multiple_sessions"
                item.pop("selected_record_id", None)
    return {"source_policy": SOURCE_POLICY, "sessions": result,
            "current_xunji_arrangements": [{"record_id": r["id"], "date": r.get("datestr"),
                "title": r.get("title"), "action_keys": action_keys(r),
                "actions": r.get("source_actions", r.get("actions", []))}
                for r in active if not r.get("is_completed")],
            "deleted_record_ids": [r["id"] for r in records if deleted(r)],
            "coverage": "supplied_fact_windows_only"}
