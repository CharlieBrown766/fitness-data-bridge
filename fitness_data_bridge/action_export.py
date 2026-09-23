"""Extract an auditable identity candidate from a Xunji React Native bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


STRING = r'"(?:[^"\\]|\\.)*"'


def extract_actions(text, app_version):
    # Module-local marker is semantic; minifier variable names are not stable.
    modules = [line for line in text.splitlines() if "movementMap" in line and "sysLength:" in line]
    if len(modules) != 1:
        raise ValueError("Expected exactly one movementMap/sysLength module; inspect new bundle format")
    module = modules[0]
    ids = list(re.finditer(r'\bid:(' + STRING + ')', module))
    actions = []
    for index, match in enumerate(ids):
        end = ids[index + 1].start() if index + 1 < len(ids) else len(module)
        # Bounded by next identity as well as the object end: never pair two objects.
        fragment = module[match.end():min(end, match.end() + 900)].split('}', 1)[0]
        label = re.search(r'\bname:(' + STRING + ')', fragment)
        if label is None:
            raise ValueError("Unpaired action id; inspect the module instead of exporting a partial catalog")
        actions.append({"key": json.loads(match.group(1)), "label": json.loads(label.group(1))})
    if not actions or not {"squat", "benchpress", "deadlift", "legpress_machine"} <= {a["key"] for a in actions}:
        raise ValueError("Incomplete movement catalog")
    by_key = {}
    for action in actions:
        by_key.setdefault(action["key"], []).append(action["label"])
    return {"schema_version": 1, "status": "candidate_only", "source_app_version": app_version,
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "entry_count": len(actions), "unique_key_count": len(by_key), "actions": actions,
            "duplicate_keys": {k: v for k, v in by_key.items() if len(v) > 1}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = Path(args.bundle).read_bytes()
    result = extract_actions(data.decode("utf-8"), args.app_version)
    result["source_sha256"] = hashlib.sha256(data).hexdigest()
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "entry_count", "unique_key_count", "source_sha256")}))


if __name__ == "__main__":
    main()
