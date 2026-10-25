"""MaritimeGuard validator: pure, deterministic, stdlib only. The LLM proposes, this decides."""
import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP

R_NM = 3440.065
V_CODES = ["ROUTE_DISCONTINUOUS", "GEO_IMPOSSIBLE", "LAND_CROSSING", "DRAFT_EXCEEDED", "AIR_DRAFT_EXCEEDED",
           "RESTRICTED_ZONE", "WEATHER_LIMIT", "PORT_CLOSED", "COST_THRESHOLD", "NEGATIVE_NET_BENEFIT",
           "MISSING_DATA", "CONTRADICTORY_DATA"]
ZONE_CODE = {"storm": "RESTRICTED_ZONE", "restricted": "RESTRICTED_ZONE", "closed_port": "PORT_CLOSED",
             "land": "LAND_CROSSING"}
METRIC_KEYS = ["original_distance_nm", "proposed_distance_nm", "extra_time_h", "extra_fuel_t",
               "extra_fuel_cost", "delay_cost_avoided", "net_benefit"]
HARD = {"ROUTE_DISCONTINUOUS", "GEO_IMPOSSIBLE", "LAND_CROSSING", "DRAFT_EXCEEDED", "AIR_DRAFT_EXCEEDED",
        "RESTRICTED_ZONE", "WEATHER_LIMIT", "PORT_CLOSED"}


def haversine_nm(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * R_NM * math.asin(math.sqrt(a))


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class Ctx:
    def __init__(self):
        self.viol = set()
        self.issues = set()

    def v(self, code, seg, detail):
        self.viol.add((code, seg or 0, detail))

    def d(self, code, field, detail):
        self.issues.add((code, field, detail))

    def num(self, obj, key, field, positive=False, required=True):
        """Return a validated float or None (recording a data issue)."""
        val = obj.get(key) if isinstance(obj, dict) else None
        if val is None:
            if required:
                self.d("MISSING_DATA", field, "missing or null")
            return None
        if not _isnum(val):
            self.d("CONTRADICTORY_DATA", field, "not a finite number")
            return None
        if val < 0 or (positive and val == 0):
            self.d("CONTRADICTORY_DATA", field, "invalid value %s" % val)
            return None
        return float(val)


def canonical_route(waypoints, segments):
    out = []
    for s in segments:
        dist = s.get("distance_nm")
        row = {"from": s.get("from"), "to": s.get("to"),
               "distance_nm": "%.3f" % float(dist) if _isnum(dist) else None}
        for k in ("from", "to"):
            w = waypoints.get(s.get(k)) or {}
            if _isnum(w.get("lat")) and _isnum(w.get("lon")):
                row[k + "_pos"] = ["%.6f" % w["lat"], "%.6f" % w["lon"]]
        out.append(row)
    return json.dumps(out, sort_keys=True, separators=(",", ":"))


def route_hash(waypoints, segments):
    return hashlib.sha256(canonical_route(waypoints, segments).encode()).hexdigest()


def r2(x):
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0


def check_route(c, name, route, wps, origin, dest, proposed):
    """Structural + per-segment checks. Returns (segments, total_distance or None)."""
    segs = route.get("segments") if isinstance(route, dict) else None
    if not isinstance(segs, list) or not segs or not all(isinstance(s, dict) for s in segs):
        c.d("MISSING_DATA", name + ".segments", "missing or empty")
        return [], None
    total, ok = 0.0, True
    for i, s in enumerate(segs, 1):
        dist = c.num(s, "distance_nm", "%s.segment[%d].distance_nm" % (name, i), positive=True)
        if dist is None:
            ok = False
        else:
            total += dist
        for k in ("from", "to"):
            if s.get(k) not in wps:
                c.d("MISSING_DATA", "%s.segment[%d].%s" % (name, i, k), "undefined waypoint %r" % (s.get(k),))
        pts = []
        for w in (wps.get(s.get("from")), wps.get(s.get("to"))):
            if isinstance(w, dict) and ("lat" in w or "lon" in w):
                la, lo = w.get("lat"), w.get("lon")
                if not (_isnum(la) and _isnum(lo)):
                    c.d("MISSING_DATA", "%s.segment[%d].coords" % (name, i), "incomplete coordinates")
                    pts = []
                    break
                if not (-90 <= la <= 90 and -180 <= lo <= 180):
                    if proposed:
                        c.v("GEO_IMPOSSIBLE", i, "coordinates out of range (%s, %s)" % (la, lo))
                    else:
                        c.d("CONTRADICTORY_DATA", "original_route.segment[%d]" % i, "coordinates out of range")
                    pts = []
                    break
                pts.append((la, lo))
        if len(pts) == 2 and dist:
            h = haversine_nm(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
            if abs(h - dist) > 0.02 * max(h, dist):
                c.d("CONTRADICTORY_DATA", "%s.segment[%d].distance_nm" % (name, i),
                    "stated %.2f vs haversine %.2f" % (dist, h))
    for i in range(len(segs) - 1):
        if segs[i].get("to") != segs[i + 1].get("from"):
            if proposed:
                c.v("ROUTE_DISCONTINUOUS", i + 2, "segment %d starts at %r but segment %d ends at %r" % (
                    i + 2, segs[i + 1].get("from"), i + 1, segs[i].get("to")))
            else:
                c.d("CONTRADICTORY_DATA", name, "discontinuous at segment %d" % (i + 2))
    if segs[0].get("from") != origin or segs[-1].get("to") != dest:
        if proposed:
            c.v("ROUTE_DISCONTINUOUS", 1, "route does not run origin %r -> destination %r" % (origin, dest))
        else:
            c.d("CONTRADICTORY_DATA", name, "does not run origin -> destination")
    stated = route.get("total_distance_nm")
    if stated is not None:
        if not _isnum(stated) or stated <= 0:
            c.d("CONTRADICTORY_DATA", name + ".total_distance_nm", "invalid")
        elif ok and abs(stated - total) > 0.01 * max(stated, total):
            c.d("CONTRADICTORY_DATA", name + ".total_distance_nm",
                "stated %.2f vs segment sum %.2f" % (stated, total))
    return segs, (total if ok else None)


def validate(t):
    c = Ctx()
    if not isinstance(t, dict):
        c.d("MISSING_DATA", "input", "not an object")
        return finish(c, None, None, None, ({}, []))
    veh = t.get("vessel") if isinstance(t.get("vessel"), dict) else {}
    if not veh:
        c.d("MISSING_DATA", "vessel", "missing")
    wps = t.get("waypoints") if isinstance(t.get("waypoints"), dict) else {}
    if not wps:
        c.d("MISSING_DATA", "waypoints", "missing")
    origin, dest = t.get("origin"), t.get("destination")
    if not origin or not dest:
        c.d("MISSING_DATA", "origin/destination", "missing")
    zones = t.get("zones") if isinstance(t.get("zones"), dict) else {}

    draft = c.num(veh, "draft_m", "vessel.draft_m", positive=True)
    speed = c.num(veh, "service_speed_kn", "vessel.service_speed_kn", positive=True)
    ukc_pct = c.num(veh, "ukc_pct", "vessel.ukc_pct", required=False)
    ukc = (10.0 if ukc_pct is None else ukc_pct) / 100.0
    cons_day = c.num(veh, "fuel_consumption_t_per_day", "vessel.fuel_consumption", required=False)
    cons_nm = c.num(veh, "fuel_consumption_t_per_nm", "vessel.fuel_consumption", required=False)
    if cons_day is None and cons_nm is None and not any(i[1] == "vessel.fuel_consumption" for i in c.issues):
        c.d("MISSING_DATA", "vessel.fuel_consumption", "need fuel_consumption_t_per_day or _per_nm")
    price = c.num(t, "fuel_price_per_t", "fuel_price_per_t")
    dch = c.num(t, "delay_cost_per_hour", "delay_cost", required=False)
    dcd = c.num(t, "delay_cost_per_day", "delay_cost", required=False)
    if dch is None and dcd is None and not any(i[1] == "delay_cost" for i in c.issues):
        c.d("MISSING_DATA", "delay_cost", "need delay_cost_per_hour or _per_day")
    delay_h = c.num(t, "delay_if_not_rerouted_h", "delay_if_not_rerouted_h")
    th = t.get("thresholds") if isinstance(t.get("thresholds"), dict) else {}
    auth = c.num(th, "authorized_extra_cost", "thresholds.authorized_extra_cost")
    band = c.num(th, "human_approval_max_extra_cost", "thresholds.human_approval_max_extra_cost", required=False)
    other = 0.0
    oc = t.get("other_costs", [])
    if not isinstance(oc, list):
        c.d("CONTRADICTORY_DATA", "other_costs", "not a list")
        oc = []
    for j, o in enumerate(oc):
        other += c.num(o, "amount", "other_costs[%d].amount" % j) or 0.0

    _, odist = check_route(c, "original_route", t.get("original_route"), wps, origin, dest, False)
    psegs, pdist = check_route(c, "proposed_route", t.get("proposed_route"), wps, origin, dest, True)

    max_wave = c.num(veh, "max_wave_m", "vessel.max_wave_m", required=False)
    max_wind = c.num(veh, "max_wind_kn", "vessel.max_wind_kn", required=False)
    for i, s in enumerate(psegs, 1):
        depth = c.num(s, "min_depth_m", "proposed_route.segment[%d].min_depth_m" % i)
        if depth is not None and draft is not None and draft * (1 + ukc) > depth + 1e-9:
            c.v("DRAFT_EXCEEDED", i, "draft %.2f m + %.0f%% UKC = %.2f m > depth %.2f m" % (
                draft, ukc * 100, draft * (1 + ukc), depth))
        if s.get("bridge_clearance_m") is not None:
            bc = c.num(s, "bridge_clearance_m", "proposed_route.segment[%d].bridge_clearance_m" % i)
            ad = c.num(veh, "air_draft_m", "vessel.air_draft_m")
            if bc is not None and ad is not None and not ad < bc:
                c.v("AIR_DRAFT_EXCEEDED", i, "air draft %.2f m >= clearance %.2f m" % (ad, bc))
        for key, vk, lbl in (("max_beam_m", "beam_m", "beam"), ("max_loa_m", "loa_m", "LOA")):
            if s.get(key) is not None:
                lim = c.num(s, key, "proposed_route.segment[%d].%s" % (i, key))
                val = c.num(veh, vk, "vessel." + vk)
                if lim is not None and val is not None and val > lim:
                    c.v("RESTRICTED_ZONE", i, "%s %.2f m exceeds limit %.2f m" % (lbl, val, lim))
        w = s.get("weather")
        if not isinstance(w, dict):
            c.d("MISSING_DATA", "proposed_route.segment[%d].weather" % i, "weather data missing")
        else:
            wave = c.num(w, "wave_m", "proposed_route.segment[%d].weather.wave_m" % i)
            wind = c.num(w, "wind_kn", "proposed_route.segment[%d].weather.wind_kn" % i)
            if max_wave is None or max_wind is None:
                c.d("MISSING_DATA", "vessel.max_wave_m/max_wind_kn", "weather limits missing")
            if wave is not None and max_wave is not None and wave > max_wave:
                c.v("WEATHER_LIMIT", i, "wave %.2f m > limit %.2f m" % (wave, max_wave))
            if wind is not None and max_wind is not None and wind > max_wind:
                c.v("WEATHER_LIMIT", i, "wind %.2f kn > limit %.2f kn" % (wind, max_wind))
        for zid in (s.get("zones") or []):
            z = zones.get(zid)
            if not isinstance(z, dict) or z.get("type") not in ZONE_CODE:
                c.d("MISSING_DATA", "zones." + str(zid), "undefined or unknown zone type")
            elif z.get("active", True) is not False:
                c.v(ZONE_CODE[z["type"]], i, "segment intersects %s zone %s" % (z["type"], zid))
        if s.get("crosses_land") is True:
            c.v("LAND_CROSSING", i, "segment crosses a landmass")

    metrics, extra_cost = None, None
    if None not in (odist, pdist, speed, price, delay_h, auth) and (cons_day is not None or cons_nm is not None) \
            and (dch is not None or dcd is not None):
        extra_t = pdist / speed - odist / speed
        extra_fuel = extra_t / 24 * cons_day if cons_day is not None else (pdist - odist) * cons_nm
        fuel_cost = extra_fuel * price
        avoided = (delay_h - extra_t) * (dch if dch is not None else dcd / 24)
        net = avoided - fuel_cost - other
        extra_cost = fuel_cost + other
        metrics = dict(zip(METRIC_KEYS, (r2(odist), r2(pdist), r2(extra_t), r2(extra_fuel), r2(fuel_cost),
                                         r2(avoided), r2(net))))
        if extra_cost > auth:
            c.v("COST_THRESHOLD", None, "extra cost %.2f > authorized %.2f" % (extra_cost, auth))
        if net < 0:
            c.v("NEGATIVE_NET_BENEFIT", None, "net benefit %.2f < 0" % net)
    return finish(c, metrics, band, extra_cost, (wps, psegs))


def finish(c, metrics, band, extra_cost, route):
    viol = sorted(c.viol, key=lambda x: (V_CODES.index(x[0]), x[1], x[2]))
    issues = sorted(c.issues, key=lambda x: (V_CODES.index(x[0]), x[1], x[2]))
    codes = {v[0] for v in viol}
    if codes & HARD:
        dec = "REJECT"
    elif issues:
        dec = "ESCALATE"
    elif codes:
        in_band = band is not None and (extra_cost is None or extra_cost <= band)
        dec = "ESCALATE" if in_band else "REJECT"
    else:
        dec = "APPROVE"
    if viol:
        reason = "%s: %s%s." % (dec, viol[0][0], " on segment %d" % viol[0][1] if viol[0][1] else "")
    elif issues:
        reason = "%s: %s (%s)." % (dec, issues[0][0], issues[0][1])
    else:
        reason = "APPROVE: all safety, data and cost checks passed."
    return {"decision": dec,
            "violations": [{"code": a, "segment": b or None, "detail": d} for a, b, d in viol],
            "data_issues": [{"code": a, "field": b, "detail": d} for a, b, d in issues],
            "metrics": metrics or {k: None for k in METRIC_KEYS},
            "route_hash": route_hash(*route) if dec == "APPROVE" else None,
            "reason": reason}
