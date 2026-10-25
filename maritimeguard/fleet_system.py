"""Fleet-system stub: python -m maritimeguard.fleet_system authorize <route.json> <route_hash>
route.json = {"waypoints": {...}, "segments": [...]}. Recomputes the hash; refuses on mismatch."""
import json
import sys

from .validator import route_hash


def main():
    if len(sys.argv) != 4 or sys.argv[1] != "authorize":
        print(json.dumps({"authorized": False, "error": "usage: authorize <route.json> <route_hash>"}))
        return 2
    try:
        with open(sys.argv[2], encoding="utf-8") as f:
            r = json.load(f)
        actual = route_hash(r["waypoints"], r["segments"])
    except Exception as e:
        print(json.dumps({"authorized": False, "error": type(e).__name__}))
        return 1
    if actual != sys.argv[3]:
        print(json.dumps({"authorized": False, "error": "HASH_MISMATCH"}))
        return 1
    with open("authorized_route.json", "w", encoding="utf-8") as f:
        json.dump({"route_hash": actual, **r}, f, sort_keys=True)
    print(json.dumps({"authorized": True, "route_hash": actual}))
    return 0


sys.exit(main())
