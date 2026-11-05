#!/usr/bin/env python3
"""Build route_first_last.json from Transport Department GTFS (pt-headway).

Source: https://static.data.gov.hk/td/pt-headway-tc/gtfs.zip (biweekly).

Output:
  {
    "version": 1,
    "source": "<url>",
    "generated": "<UTC ISO8601>",
    "routes": {
      "KMB|1|O": {"first": "05:35", "last": "23:10", "orig": "竹園邨", "dest": "尖沙咀碼頭"},
      "KMB|1|I": {"first": "06:30", "last": "23:05", "orig": "尖沙咀碼頭", "dest": "竹園邨"},
      "GMB|2000511|O": {"first": "06:30", "last": "23:30", "orig": "…", "dest": "…"}
    }
  }

Key scheme
----------
- Franchised bus (KMB / CTB / LWB): ``COMPANY|ROUTE|BOUND``
  - LWB agency rows are emitted as **KMB** (BusGo stores LWB under KMB).
  - Joint ``KMB+CTB`` / ``LWB+CTB`` rows are duplicated under **KMB** and **CTB**.
- GMB: ``GMB|<route_id>|BOUND`` — route_id matches data.etagmb.gov.hk / gmb_fares
  (route short names collide across regions).
- NLB: ``NLB|ROUTE|BOUND`` with O/I from long_name (BusGo NLB rows often keep bound=O
  and distinguish directions via serviceType/routeId — the app matches by orig/dest).

Bound O/I
---------
Parse ``route_long_name`` as ``A - B`` (first `` - ``). Direction whose stop_sequence=1
origin name best matches A → O (A→B); match B → I (B→A). If matching fails, sort
distinct origin stop_ids and assign O then I (deterministic).

First / last
------------
Min / max departure at the origin for **weekday** calendar services (monday–friday all 1).
If a route has no Mon–Fri-all service, fall back to any service that includes a weekday,
then any service.

Departures come from ``stop_times`` at ``stop_sequence=1``, plus ``frequencies.txt``
windows (each window contributes ``start_time`` and last start ``end_time - headway``).

Times are HH:MM. Hours ≥ 24 (past midnight) are stored modulo 24 (e.g. 25:10 → 01:10).

Variants / serviceType
----------------------
GTFS may have several route_ids for one short_name (special variants). v1 **merges**
them per COMPANY|ROUTE|BOUND (earliest first / latest last) and keeps orig/dest from
the widest-span variant. KMB multi-serviceType numbers are **not** encoded in the key
(no stable GTFS mapping) — all serviceTypes share that merged window.

Only agencies used by BusGo are emitted (KMB/CTB/LWB/NLB/GMB and joints).

Manual overrides
----------------
``overrides/route_first_last_overrides.json`` is merged after the GTFS pass. Override
``routes`` keys replace GTFS values (extra keys such as ``source`` / ``as_of`` stay in the
overrides file only and are stripped from the published JSON). Use this for routes missing
from TD GTFS (e.g. NLB B12).
"""
from __future__ import annotations

import csv
import os
import io
import json
import sys
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

SOURCE = "https://static.data.gov.hk/td/pt-headway-tc/gtfs.zip"
FORMAT_VERSION = 1
USER_AGENT = "busgo-data"

# GTFS agency_id → one or more BusGo company codes used in lookup keys
AGENCY_COMPANIES = {
    "KMB": ["KMB"],
    "CTB": ["CTB"],
    "LWB": ["KMB"],  # BusGo folds LWB into KMB route lists
    "KMB+CTB": ["KMB", "CTB"],
    "LWB+CTB": ["KMB", "CTB"],
    "NLB": ["NLB"],
    "GMB": ["GMB"],
}


def download_zip() -> bytes:
    req = urllib.request.Request(SOURCE, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def read_csv(zf: zipfile.ZipFile, name: str) -> List[dict]:
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        return list(csv.DictReader(text))


def parse_hhmmss(value: str) -> Optional[int]:
    """Return minutes from midnight (may exceed 24h)."""
    parts = (value or "").strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return h * 60 + m


def fmt_hhmm(minutes: int) -> str:
    minutes = minutes % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def split_long_name(long_name: str) -> Tuple[str, str]:
    name = (long_name or "").strip()
    if " - " in name:
        a, b = name.split(" - ", 1)
        return a.strip(), b.strip()
    return name, ""


def strip_paren(name: str) -> str:
    """Drop trailing （…） / (...) annotations for fuzzy origin matching."""
    s = name
    for left, right in (("（", "）"), ("(", ")"), ("[", "]")):
        while left in s and right in s:
            i, j = s.find(left), s.find(right, s.find(left))
            if i < 0 or j < 0:
                break
            s = (s[:i] + s[j + len(right) :]).strip()
    return s.replace("<BR>", "").strip()


def stop_display_name(raw: str) -> str:
    """GTFS stop_name may be ``[KMB] 竹園邨總站|[CTB] …`` — prefer KMB/CTB/NLB/GMB tag."""
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    preferred_tags = ("[KMB]", "[CTB]", "[NLB]", "[GMB]", "[LWB]")
    for tag in preferred_tags:
        for p in parts:
            if p.startswith(tag):
                return p[len(tag) :].strip().replace("<BR>", " ").strip()
    # fall back: strip any leading [TAG]
    first = parts[0] if parts else raw
    if first.startswith("[") and "]" in first:
        return first.split("]", 1)[1].strip().replace("<BR>", " ").strip()
    return first.replace("<BR>", " ").strip()


def name_matches(origin: str, endpoint: str) -> bool:
    if not origin or not endpoint:
        return False
    o = strip_paren(origin)
    e = strip_paren(endpoint)
    if not o or not e:
        return False
    return o == e or o.startswith(e) or e.startswith(o) or e in o or o in e


def weekday_service_ids(calendar_rows: Sequence[dict]) -> Tuple[Set[str], Set[str], Set[str]]:
    """Return (mon_fri_all, any_weekday, all_services)."""
    mon_fri_all: Set[str] = set()
    any_weekday: Set[str] = set()
    all_services: Set[str] = set()
    days = ("monday", "tuesday", "wednesday", "thursday", "friday")
    for row in calendar_rows:
        sid = row["service_id"]
        all_services.add(sid)
        flags = [row.get(d, "0") == "1" for d in days]
        if any(flags):
            any_weekday.add(sid)
        if all(flags):
            mon_fri_all.add(sid)
    return mon_fri_all, any_weekday, all_services


def pick_services(available: Set[str], mon_fri_all: Set[str], any_weekday: Set[str], all_services: Set[str]) -> Set[str]:
    for pool in (mon_fri_all, any_weekday, all_services):
        hit = available & pool
        if hit:
            return hit
    return available


def assign_bounds(
    long_name: str,
    origins: Dict[str, str],
) -> Dict[str, str]:
    """Map origin_stop_id → 'O'/'I'."""
    a, b = split_long_name(long_name)
    origin_ids = sorted(origins.keys())
    if len(origin_ids) == 1:
        # Circular / one-way in feed: treat as outbound
        return {origin_ids[0]: "O"}

    result: Dict[str, str] = {}
    used = set()
    # Try match A → O, B → I
    for sid in origin_ids:
        name = origins[sid]
        if "O" not in used and name_matches(name, a):
            result[sid] = "O"
            used.add("O")
        elif "I" not in used and name_matches(name, b):
            result[sid] = "I"
            used.add("I")
    # Remaining / failed: deterministic by stop_id order
    for sid in origin_ids:
        if sid in result:
            continue
        for bound in ("O", "I"):
            if bound not in used:
                result[sid] = bound
                used.add(bound)
                break
        else:
            # >2 origins: fold extras into I
            result[sid] = "I"
    return result


def endpoint_labels(long_name: str, bound: str, origin_name: str) -> Tuple[str, str]:
    a, b = split_long_name(long_name)
    if bound == "O":
        return (origin_name or a, b or "")
    return (origin_name or b, a or "")


def companies_for(agency_id: str) -> List[str]:
    return AGENCY_COMPANIES.get(agency_id, [])


def build(zf: zipfile.ZipFile) -> dict:
    agencies = {r["agency_id"]: r for r in read_csv(zf, "agency.txt")}
    calendar = read_csv(zf, "calendar.txt")
    mon_fri_all, any_weekday, all_services = weekday_service_ids(calendar)

    routes_all = read_csv(zf, "routes.txt")
    routes = [r for r in routes_all if r["agency_id"] in AGENCY_COMPANIES]
    route_by_id = {r["route_id"]: r for r in routes}
    wanted_route_ids = set(route_by_id)

    trips = [t for t in read_csv(zf, "trips.txt") if t["route_id"] in wanted_route_ids]
    trip_meta = {t["trip_id"]: t for t in trips}

    stops = {s["stop_id"]: stop_display_name(s.get("stop_name", "")) for s in read_csv(zf, "stops.txt")}

    # trip_id → (departure_minutes, origin_stop_id)
    first_dep: Dict[str, Tuple[int, str]] = {}
    with zf.open("stop_times.txt") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        for row in reader:
            if row.get("stop_sequence") != "1":
                continue
            tid = row["trip_id"]
            if tid not in trip_meta:
                continue
            mins = parse_hhmmss(row.get("departure_time") or row.get("arrival_time") or "")
            if mins is None:
                continue
            first_dep[tid] = (mins, row["stop_id"])

    # frequencies.txt: headway windows expand first/last beyond the single stop_times row
    # trip_id → list of extra departure minutes (start + last start before end)
    freq_extra: Dict[str, List[int]] = defaultdict(list)
    if "frequencies.txt" in zf.namelist():
        with zf.open("frequencies.txt") as raw:
            text_f = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            for row in csv.DictReader(text_f):
                tid = row.get("trip_id") or ""
                if tid not in trip_meta:
                    continue
                start = parse_hhmmss(row.get("start_time") or "")
                end = parse_hhmmss(row.get("end_time") or "")
                try:
                    headway_min = max(1, int(row.get("headway_secs") or "60") // 60)
                except ValueError:
                    headway_min = 1
                if start is None:
                    continue
                freq_extra[tid].append(start)
                if end is not None:
                    last_start = end - headway_min
                    if last_start < start:
                        last_start = start
                    freq_extra[tid].append(last_start)

    # Group trips by route_id → origin_stop → list of (mins, service_id)
    # route_id → origin_stop_id → [(mins, service_id), ...]
    by_route_origin: Dict[str, Dict[str, List[Tuple[int, str]]]] = defaultdict(lambda: defaultdict(list))
    services_per_route: Dict[str, Set[str]] = defaultdict(set)
    for tid, (mins, stop_id) in first_dep.items():
        t = trip_meta[tid]
        rid = t["route_id"]
        sid = t["service_id"]
        by_route_origin[rid][stop_id].append((mins, sid))
        for extra in freq_extra.get(tid, []):
            by_route_origin[rid][stop_id].append((extra, sid))
        services_per_route[rid].add(sid)

    # Score each GTFS route_id by weekday trip count (for variant picking)
    def weekday_trip_count(route_id: str) -> int:
        pool = pick_services(services_per_route[route_id], mon_fri_all, any_weekday, all_services)
        n = 0
        for trips_at_origin in by_route_origin[route_id].values():
            n += sum(1 for _, sid in trips_at_origin if sid in pool)
        return n

    # Candidate entries: key → ranking payload
    # Multiple GTFS route_ids can share COMPANY|ROUTE|BOUND (special variants).
    # Merge first=min / last=max across variants; keep orig/dest from the widest span.
    # rank = (span_minutes, weekday_trip_count)
    candidates: Dict[str, Tuple[Tuple[int, int], int, int, dict]] = {}
    # value: (rank, first_mins, last_mins, payload)

    for route in routes:
        rid = route["route_id"]
        agency = route["agency_id"]
        companies = companies_for(agency)
        if not companies:
            continue
        origins_raw = by_route_origin.get(rid) or {}
        if not origins_raw:
            continue

        long_name = route.get("route_long_name") or ""
        short_name = (route.get("route_short_name") or "").strip()
        origin_names = {sid: stops.get(sid, "") for sid in origins_raw}
        bound_map = assign_bounds(long_name, origin_names)
        pool = pick_services(services_per_route[rid], mon_fri_all, any_weekday, all_services)
        score = weekday_trip_count(rid)

        for origin_sid, trips_list in origins_raw.items():
            bound = bound_map.get(origin_sid, "O")
            mins_list = [m for m, sid in trips_list if sid in pool]
            if not mins_list:
                mins_list = [m for m, _ in trips_list]
            if not mins_list:
                continue
            first_m, last_m = min(mins_list), max(mins_list)
            span = max(0, last_m - first_m)
            orig_name = origin_names.get(origin_sid, "")
            orig_label, dest_label = endpoint_labels(long_name, bound, orig_name)
            payload = {
                "first": fmt_hhmm(first_m),
                "last": fmt_hhmm(last_m),
                "orig": orig_label,
                "dest": dest_label,
            }
            rank = (span, score)

            for company in companies:
                if company == "GMB":
                    key = f"GMB|{rid}|{bound}"
                else:
                    if not short_name:
                        continue
                    key = f"{company}|{short_name}|{bound}"
                prev = candidates.get(key)
                if prev is None:
                    candidates[key] = (rank, first_m, last_m, payload)
                    continue
                prev_rank, prev_first, prev_last, prev_payload = prev
                merged_first = min(prev_first, first_m)
                merged_last = max(prev_last, last_m)
                # Labels from the wider / busier variant
                label_payload = payload if rank > prev_rank else prev_payload
                candidates[key] = (
                    max(prev_rank, rank),
                    merged_first,
                    merged_last,
                    {
                        "first": fmt_hhmm(merged_first),
                        "last": fmt_hhmm(merged_last),
                        "orig": label_payload["orig"],
                        "dest": label_payload["dest"],
                    },
                )

    routes_out = {
        k: v[3] for k, v in sorted(candidates.items(), key=lambda kv: kv[0])
    }
    return {
        "version": FORMAT_VERSION,
        "source": SOURCE,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "routes": routes_out,
    }


def overrides_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "overrides",
        "route_first_last_overrides.json",
    )


def apply_overrides(doc: dict, path: Optional[str] = None) -> int:
    """Merge manual overrides into doc['routes']. Returns number of keys applied."""
    ov_path = path or overrides_path()
    if not os.path.isfile(ov_path):
        return 0
    with open(ov_path, encoding="utf-8") as f:
        ov = json.load(f)
    raw = ov.get("routes") or {}
    applied = 0
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        first, last = value.get("first"), value.get("last")
        if not first or not last:
            continue
        doc.setdefault("routes", {})[key] = {
            "first": first,
            "last": last,
            "orig": value.get("orig") or "",
            "dest": value.get("dest") or "",
        }
        applied += 1
    if applied:
        doc["routes"] = dict(sorted(doc["routes"].items(), key=lambda kv: kv[0]))
    return applied


def main(out_path: str) -> None:
    print(f"downloading {SOURCE} …", flush=True)
    blob = download_zip()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        doc = build(zf)
    n = apply_overrides(doc)
    if n:
        print(
            f"applied {n} manual override(s) from overrides/route_first_last_overrides.json",
            flush=True,
        )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")
    print(f"wrote {len(doc['routes'])} route directions to {out_path}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "route_first_last.json")
