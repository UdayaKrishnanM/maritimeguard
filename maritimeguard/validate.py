import hashlib
import json
import os
import sys

from .validator import validate, METRIC_KEYS


def record(path, out):
    """Session ledger read by the failproofai policies. Never affects stdout."""
    if os.environ.get("MARITIMEGUARD_NO_LEDGER"):
        return
    try:
        h = hashlib.sha256(open(path, "rb").read()).hexdigest()
        os.makedirs(".maritimeguard", exist_ok=True)
        led_path = os.path.join(".maritimeguard", "ledger.json")
        led = json.load(open(led_path)) if os.path.exists(led_path) else {"runs": {}}
        led["runs"][h] = True
        led["last"] = {"decision": out["decision"], "route_hash": out["route_hash"], "stdout": json.dumps(out)}
        json.dump(led, open(led_path, "w"))
    except Exception:
        pass


def main():
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            out = validate(json.load(f))
    except Exception as e:  # unreadable/invalid input or internal error => ESCALATE
        out = {"decision": "ESCALATE", "violations": [],
               "data_issues": [{"code": "MISSING_DATA", "field": "input", "detail": type(e).__name__}],
               "metrics": {k: None for k in METRIC_KEYS}, "route_hash": None,
               "reason": "ESCALATE: MISSING_DATA (input)."}
    print(json.dumps(out))
    if len(sys.argv) > 1:
        record(sys.argv[1], out)


main()
