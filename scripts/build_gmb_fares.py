#!/usr/bin/env python3
"""Build gmb_fares.json from Transport Department open data (FARE_GMB.xml).

Output format:
  {
    "version": 1,
    "source": "<url>",
    "td_generated": "<generated attribute of the XML>",
    "fares": {
      "<routeId>|<routeSeq>": 12.5,              # flat fare for every boarding stop
      "<routeId>|<routeSeq>": [null, 14.5, 9.8]   # index = stop_seq (ON_SEQ); value = max fare from that stop
    }
  }
"""
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

SOURCE = "https://static.data.gov.hk/td/routes-fares-xml/FARE_GMB.xml"
FORMAT_VERSION = 1


def main(out_path: str) -> None:
    req = urllib.request.Request(SOURCE, headers={"User-Agent": "busgo-data"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        root = ET.fromstring(resp.read())

    best = defaultdict(dict)  # (route_id, route_seq) -> {on_seq: max price}
    for row in root.iter("FARE"):
        try:
            key = (row.findtext("ROUTE_ID").strip(), int(row.findtext("ROUTE_SEQ")))
            on_seq = int(row.findtext("ON_SEQ"))
            price = float(row.findtext("PRICE"))
        except (AttributeError, TypeError, ValueError):
            continue
        if price > best[key].get(on_seq, -1.0):
            best[key][on_seq] = price

    fares = {}
    for (route_id, route_seq), by_stop in sorted(best.items(), key=lambda kv: (int(kv[0][0]), kv[0][1])):
        values = set(by_stop.values())
        if len(values) == 1:
            fares[f"{route_id}|{route_seq}"] = values.pop()
        else:
            arr = [None] * (max(by_stop) + 1)
            for seq, price in by_stop.items():
                arr[seq] = price
            fares[f"{route_id}|{route_seq}"] = arr

    doc = {
        "version": FORMAT_VERSION,
        "source": SOURCE,
        "td_generated": root.attrib.get("generated", ""),
        "fares": fares,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")
    print(f"wrote {len(fares)} route directions to {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "gmb_fares.json")
