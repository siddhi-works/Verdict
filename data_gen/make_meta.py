"""
data_gen/make_meta.py

Writes contract_meta.json next to a dataset: the business context a real console shows (who the contract is with,
where, for which period). Everything is fictional and derived from the contract id, so it is repeatable.
It is display-only: the audit engine never reads it, so verdicts and ground truth are unaffected.

    python -m data_gen.make_meta --data data/v2
"""
import argparse
import hashlib
import json
from pathlib import Path

CUSTOMERS = [
    "Sahyadri Transport Co.", "Northline Logistics", "Alpine Haulage GmbH", "Kaveri Roadways", "Rhone Valley Freight",
    "Deccan Bulk Carriers", "Bluewater Distribution", "Lombardia Trasporti", "Meridian Coach Lines", "Orchard Fresh Logistics",
    "Thar Mining Haulage", "Baltic Cold Chain", "Konkan Express Cargo", "Atlas Municipal Transit", "Harbourside Container Services",
    "Vindhya Agri Freight", "Iberia Last Mile", "Ganga Fleet Services", "Nordic Timber Haul", "Coromandel Bus Operators",
    "Pyrenees Tankers", "Malwa Dairy Logistics", "Danube Parcel Network", "Western Ghats Quarry Transport",
]
REGIONS = ["Maharashtra, India", "Karnataka, India", "Tamil Nadu, India", "Gujarat, India", "Auvergne-Rhone-Alpes, France",
           "Bavaria, Germany", "Lombardy, Italy", "Catalonia, Spain", "Hesse, Germany", "Occitanie, France"]
TITLES = ["Tyre and uptime services agreement", "Fleet service level agreement", "Managed tyre programme, long-haul fleet",
          "Urban delivery fleet service agreement", "Bus fleet availability agreement", "Mining haul fleet service agreement"]


def pick(options, cid, salt):
    h = hashlib.sha256(f"{salt}:{cid}".encode()).digest()
    return options[int.from_bytes(h[:4], "big") % len(options)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/v2")
    a = ap.parse_args()
    d = Path(a.data)
    contracts = json.loads((d / "contracts.json").read_text(encoding="utf-8"))
    meta = {}
    for c in contracts:
        cid = c["contract_id"]
        meta[cid] = {"customer": pick(CUSTOMERS, cid, "customer"), "region": pick(REGIONS, cid, "region"),
                     "title": pick(TITLES, cid, "title"), "period": "September 2026"}
    (d / "contract_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"wrote {len(meta)} records to {d / 'contract_meta.json'}")


if __name__ == "__main__":
    main()
