# MaritimeGuard

The LLM proposes, code decides. `maritimeguard/validator.py` is pure stdlib Python (no network, no randomness, no
timestamps), so the same input always gives byte-identical output.

## Agent flow (see CLAUDE.md)
Save the task input to a file, run `python -m maritimeguard.validate <file>` once, return its JSON verbatim.

## Decision precedence (fixed)
1. Any hard safety violation (ROUTE_DISCONTINUOUS, GEO_IMPOSSIBLE, LAND_CROSSING, DRAFT_EXCEEDED, AIR_DRAFT_EXCEEDED,
   RESTRICTED_ZONE, WEATHER_LIMIT, PORT_CLOSED) -> REJECT
2. Else any data issue (MISSING_DATA, CONTRADICTORY_DATA) -> ESCALATE
3. Else COST_THRESHOLD / NEGATIVE_NET_BENEFIT -> ESCALATE if `thresholds.human_approval_max_extra_cost` is given and the
   extra cost is within it (negative net benefit alone also escalates when a band is defined), otherwise REJECT
4. Else APPROVE, with `route_hash` = sha256 of the canonical route (sorted keys, 3 dp distances, 6 dp coordinates).

## Input schema (the repo had no harness, so this is our own)
`origin`, `destination`, `waypoints{name:{lat,lon}}` (coordinates optional), `vessel{draft_m, air_draft_m,
service_speed_kn, fuel_consumption_t_per_day | _per_nm, max_wave_m, max_wind_kn, ukc_pct?, beam_m?, loa_m?}`,
`original_route` / `proposed_route{total_distance_nm?, segments[{from,to,distance_nm, min_depth_m, weather{wave_m,wind_kn},
zones[], bridge_clearance_m?, max_beam_m?, max_loa_m?, crosses_land?}]}`, `zones{id:{type: storm|restricted|closed_port|land,
active}}`, `fuel_price_per_t`, `delay_cost_per_hour | _per_day`, `delay_if_not_rerouted_h`, `other_costs[{amount}]`,
`thresholds{authorized_extra_cost, human_approval_max_extra_cost?}`. `notes` is never read.

## Assumptions
- UKC defaults to 10% of draft. Draft check: `draft*(1+ukc) <= min_depth`.
- Distances: 1% tolerance on stated total vs segment sum, 2% on haversine (R=3440.065 nm) vs stated segment distance.
- Weather is required on every proposed segment; absent weather or limits -> MISSING_DATA, never assumed calm.
- Extra cost for the threshold = extra fuel cost + other_costs. Net = delay avoided - extra fuel cost - other_costs.
- Metrics are rounded to 2 dp only at the end (Decimal, half-up). If cost inputs are incomplete, metrics are null.
- Segment numbers in violations are 1-based. A bad shared waypoint is reported on every segment that uses it.
- Validator crash or unreadable input -> ESCALATE / MISSING_DATA.

## Evals
`python evals/run_evals.py` runs the 10 cases (plus 05b) three times each through the real CLI, asserts decision,
violation codes, data issues and metrics (tolerance 0.01), and checks the three outputs are byte-identical.
"Baseline fails" is by design (24x fuel trap, UKC arithmetic, injection, "assume calm"), not measured here.

## fleet system and failproofai
- `python -m maritimeguard.fleet_system authorize <route.json> <route_hash>` recomputes the hash and refuses on mismatch.
- Built-in policies enabled (project scope): block-sudo, block-rm-rf, block-curl-pipe-sh, sanitize-api-keys,
  sanitize-jwt, block-env-files, warn-repeated-tool-calls.
- Custom policies: `policies/maritimeguard-policies.mjs` (no-unsafe-retry, no-unverified-authorization,
  validator-is-source-of-truth). The validator writes `.maritimeguard/ledger.json`; the policies read it.
  Install: `failproofai policies --install --custom ./policies/maritimeguard-policies.mjs`.
  The older copy in `.failproofai/policies/` relies on undocumented hook fields; replace it with this one.
