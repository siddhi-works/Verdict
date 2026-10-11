"""
data_gen/generate.py

Builds a realistic, labelled test set where the correct answer is known BY
CONSTRUCTION: each scenario starts from the facts (thresholds, which clause
governs, what the fleet really did), and only then renders them into long
legal prose and a noisy telemetry log. The answers are written to a separate
ground_truth.json, never into the contract file, so the system cannot peek.

    python -m data_gen.generate --n 200 --seed 7 --out data/v2

Scenario families (see CATEGORIES): clean contracts, scoped repair clauses,
contradictions that do / do not change the outcome, precedence clauses,
vague wording, missing or dirty telemetry, planned-maintenance exclusions,
numbers written in words or days, and a few traps for naive pattern matching.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ prose

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}


def words(n: float) -> str:
    if abs(n - round(n)) > 1e-9:
        whole, frac = str(n).split(".")
        return words(int(whole)) + " point " + " ".join(_ONES[int(d)] for d in frac)
    n = int(round(n))
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = (n // 10) * 10, n % 10
        return _TENS[t] + (f"-{_ONES[o]}" if o else "")
    return f"{words(n // 100)} hundred" + (f" {words(n % 100)}" if n % 100 else "")


def g(x: float) -> str:
    return f"{x:g}"


def uptime_sentence(rng, u: float, style: str) -> str:
    num = f"{words(u)} percent" if style == "words" else f"{g(u)}%"
    return rng.choice([
        f"Monthly fleet uptime shall be maintained at not less than {num}.",
        f"The Provider guarantees fleet uptime of at least {num} in each calendar month.",
        f"Covered vehicles shall achieve an average operational uptime of {num} or better.",
    ])


def hours_text(h: float, style: str) -> str:
    if style == "days" and h % 24 == 0:
        d = int(h // 24)
        return f"{words(d)} day{'s' if d != 1 else ''}"
    if style == "words":
        return f"{words(h)} hours"
    return f"{g(h)} hours"


def repair_sentence(rng, h: float, style: str, scoped: bool = True) -> str:
    t = hours_text(h, style)
    if not scoped:
        return rng.choice([f"Any reported vehicle fault shall be repaired within {t} of notification.",
                           f"The Provider shall restore each faulty covered vehicle within {t}."])
    return rng.choice([
        f"Each Critical Event shall be restored to operational status within {t} of validated notification.",
        f"The Provider shall repair every Critical Event within {t} of notification.",
        f"Critical faults must be resolved within {t}.",
    ])


FILLER = [
    "Invoices are payable within thirty (30) days of receipt; overdue amounts bear interest at 1.5% per month.",
    "The Provider shall maintain commercial general liability insurance of not less than $2,000,000 per occurrence.",
    "This Agreement has an initial term of 36 months and renews for successive 12 month terms unless either party gives 90 days notice.",
    "Either party may terminate this Agreement for uncured material breach upon 30 days written notice.",
    "Each party shall keep the other's Confidential Information secret for 5 years after the end of the term.",
    "Neither party's total liability shall exceed the fees paid in the preceding 12 months.",
    "Personal data processed under this Agreement shall be deleted within 60 days after termination.",
    "Neither party is liable for delay caused by events beyond its reasonable control lasting up to 45 consecutive days.",
    "The Customer may audit the Provider's service records once per calendar year on 15 days notice.",
    "Notices must be in writing and are deemed received 5 business days after dispatch.",
    "This Agreement is governed by the laws of the jurisdiction stated in the Order Form.",
    "The Provider may subcontract non-core services provided it remains responsible for their performance.",
]
BOILERPLATE = [
    "The Provider is an independent contractor and nothing in this Agreement creates a partnership, joint venture or employment relationship between the parties.",
    "The Customer shall give the Provider reasonable access to the Covered Vehicles, depots and telematics systems needed to perform the services.",
    "The Provider shall comply with all laws applicable to its performance, including road-safety, environmental and tyre-disposal regulations.",
    "Neither party may assign this Agreement without the prior written consent of the other, which shall not be unreasonably withheld.",
    "Any amendment must be in writing and signed by an authorised representative of each party; no course of dealing amends this Agreement.",
    "If any provision is held invalid, the remaining provisions continue in full force and the parties shall replace the invalid provision with a valid one of similar effect.",
    "The Provider shall keep complete and accurate books and records relating to the services and the fees charged.",
    "Intellectual property existing before this Agreement remains with its owner, and the Customer receives a non-exclusive licence to use the Provider's reporting tools during the term.",
    "The Provider shall ensure that its personnel are suitably trained, hold the required certifications and follow the Customer's site safety rules.",
    "Fees are set out in the Order Form and exclude applicable taxes, which the Customer shall pay in addition.",
    "The Provider shall not use the Customer's name or marks in publicity without prior written approval.",
    "Each party shall appoint a relationship manager who is authorised to make day-to-day decisions under this Agreement.",
    "Telemetry data generated by the Covered Vehicles belongs to the Customer; the Provider may use it only to perform the services and to improve its products in anonymised form.",
    "This Agreement, together with the Order Form and any schedules, is the entire agreement between the parties and supersedes all prior understandings on its subject.",
]
TRAP_DISPUTE = "Disputes shall first be escalated to senior management, who shall seek resolution within 30 days."
# Looks like a repair-time clause to a pattern matcher but is about warranty paperwork; needs real reading.
TRAP_WARRANTY = "Where a Covered Vehicle is replaced, the Provider shall resolve any related warranty claim within 45 days."

# ------------------------------------------------------------------ rendering


def render_contract(rng, cid: str, sla_sentences: List[str], extra_sections: List[str], trap: Optional[str]) -> str:
    numeric, boiler = list(FILLER), list(BOILERPLATE)
    rng.shuffle(numeric)
    rng.shuffle(boiler)
    numeric = numeric[: rng.randint(6, 10)]
    if trap:
        numeric.insert(rng.randint(0, len(numeric)), TRAP_WARRANTY if trap == "warranty" else TRAP_DISPUTE)
    boiler = boiler[: rng.randint(8, 14)]
    pool = numeric + boiler
    rng.shuffle(pool)
    a, b = len(pool) // 3, 2 * len(pool) // 3
    parts = [
        f"MASTER FLEET SERVICES AGREEMENT - {cid}",
        "1. DEFINITIONS. \"Critical Event\" means a fault that materially disables a covered vehicle or prevents safe dispatch. "
        "\"Covered Vehicle\" means a vehicle listed in the Order Form. \"Month\" means a calendar month.",
        "2. GENERAL TERMS. " + " ".join(pool[:a]),
        "3. SERVICE LEVELS. " + " ".join(sla_sentences),
    ]
    parts += extra_sections
    parts += [
        "4. MEASUREMENT. Uptime is calculated from Provider telemetry and validated incident records for each Covered Vehicle and "
        "averaged across the fleet. Provider shall retain service records and make them available on reasonable request.",
        "5. COMMERCIAL TERMS. " + " ".join(pool[a:b]),
        "6. GENERAL PROVISIONS. " + " ".join(pool[b:]),
    ]
    return "\n\n".join(parts)


# ------------------------------------------------------------------ telemetry

EVENT_MAP = {"engine_fault": "critical", "brake_fault": "critical", "pressure_drop": "medium", "tire_wear": "medium"}
COLUMNS = ["log_id", "contract_id", "truck_id", "vehicle_class", "event_type", "severity", "event_start",
           "resolution_hours", "downtime_minutes", "actual_uptime_percent", "sensor_status", "planned_maintenance"]


class Fleet:
    """Builds one contract's telemetry rows from the facts we want it to show."""

    def __init__(self, rng: random.Random, nrng: np.random.Generator, cid: str, n_trucks: int):
        self.rng, self.nrng, self.cid, self.n = rng, nrng, cid, n_trucks
        self.rows: List[dict] = []
        self.truck_uptime: Dict[str, float] = {}

    def _stamp(self) -> str:
        d = self.rng.randint(1, 28)
        return f"2026-09-{d:02d}T{self.rng.randint(0, 23):02d}:{self.rng.randint(0, 59):02d}:00Z"

    def build(self, mean_uptime: float, crit_max: float, noncrit_max: float, crit_cap: float,
              planned_extra: Optional[float] = None):
        """crit_max is hit exactly once; every other critical event stays below `crit_cap`."""
        n = self.n
        noise = self.nrng.normal(0, 0.45, n)
        ups = np.clip(mean_uptime + noise - noise.mean(), 60.0, 99.99)
        for _ in range(4):  # clipping can nudge the mean; correct it
            ups = np.clip(ups + (mean_uptime - ups.mean()), 60.0, 99.99)
        ups = np.round(ups, 2)
        diff = mean_uptime * n - float(ups.sum())  # spread rounding drift so the fleet mean is exact and every value stays <= 99.99
        for i in range(n):
            if abs(diff) < 0.005:
                break
            new = float(np.clip(ups[i] + diff, 60.0, 99.99))
            diff -= new - float(ups[i])
            ups[i] = round(new, 2)
        crit_idx = self.rng.randrange(n)
        for i in range(n):
            truck = f"TRK-{i + 1:04d}"
            self.truck_uptime[truck] = float(ups[i])
            vclass = self.rng.choice(["Standard", "Premium", "Heavy"])
            kinds = [self.rng.choice(list(EVENT_MAP)) for _ in range(self.rng.randint(1, 4))] + ["normal"]
            for kind in kinds:
                self._add(truck, vclass, kind, ups[i], crit_cap, noncrit_max)
        # one critical event carries the maximum
        truck = f"TRK-{crit_idx + 1:04d}"
        self._add(truck, "Standard", "engine_fault", ups[crit_idx], crit_cap, noncrit_max, force_res=crit_max)
        if noncrit_max > 0:
            self._add(f"TRK-{self.rng.randrange(n) + 1:04d}", "Standard", "tire_wear", ups[crit_idx], crit_cap,
                      noncrit_max, force_res=noncrit_max)
        if planned_extra is not None:
            self._add(f"TRK-{self.rng.randrange(n) + 1:04d}", "Heavy", "engine_fault", ups[crit_idx], crit_cap,
                      noncrit_max, force_res=planned_extra, planned=True)

    def _add(self, truck, vclass, kind, uptime, crit_cap, noncrit_max, force_res=None, planned=False,
             status="OK", garbage=None):
        if kind == "normal":
            res, sev = None, "low"
        else:
            sev = EVENT_MAP[kind]
            if force_res is not None:
                res = float(force_res)
            elif sev == "critical":
                res = round(float(min(self.nrng.gamma(2.0, max(crit_cap, 2) / 8.0), crit_cap)), 1)
            else:
                res = round(float(self.nrng.uniform(1, max(noncrit_max, 2))), 1)
        row = {"contract_id": self.cid, "truck_id": truck, "vehicle_class": vclass, "event_type": kind,
               "severity": sev, "event_start": self._stamp(), "resolution_hours": res,
               "downtime_minutes": None if res is None else round(res * 60),
               "actual_uptime_percent": float(uptime), "sensor_status": status, "planned_maintenance": planned}
        if garbage:
            row.update(garbage)
        self.rows.append(row)

    # --- dirt -----------------------------------------------------------
    def add_unusable(self, k: int):
        for _ in range(k):
            truck = f"TRK-{self.rng.randrange(self.n) + 1:04d}"
            kind = self.rng.choice(["uptime_over", "neg_res", "nan_res"])
            up = self.truck_uptime[truck]
            if kind == "uptime_over":
                self._add(truck, "Standard", "pressure_drop", up, 10, 10, garbage={"actual_uptime_percent": round(self.rng.uniform(101, 400), 1)})
            elif kind == "neg_res":
                self._add(truck, "Standard", "engine_fault", up, 10, 10, garbage={"resolution_hours": -round(self.rng.uniform(1, 9), 1), "downtime_minutes": -30})
            else:
                self._add(truck, "Standard", "engine_fault", up, 10, 10, garbage={"resolution_hours": None, "downtime_minutes": None})

    def add_harmless_suspects(self, k: int):
        """Duplicates and FAULT-status readings that repeat values already present: they cannot change a verdict."""
        for _ in range(k):
            src = self.rng.choice(self.rows)
            self.rows.append(dict(src))
        for _ in range(max(1, k // 2)):
            src = dict(self.rng.choice(self.rows))
            src["sensor_status"] = "FAULT"
            self.rows.append(src)

    def add_decisive_fault_row(self, res: float):
        self._add(f"TRK-{self.rng.randrange(self.n) + 1:04d}", "Standard", "engine_fault", 99.0, 10, 10,
                  force_res=res, status="FAULT")

    def frame(self, log_start: int) -> pd.DataFrame:
        order = list(range(len(self.rows)))
        self.rng.shuffle(order)
        rows = [self.rows[i] for i in order]
        df = pd.DataFrame(rows)
        df.insert(0, "log_id", range(log_start, log_start + len(df)))
        return df[COLUMNS]


# ------------------------------------------------------------------ scenarios


def fleet_size(rng) -> int:
    return int(round(float(np.exp(rng.uniform(np.log(6), np.log(600))))))


def pick_style(rng, allow=("digits", "digits", "digits", "words", "days")) -> str:
    return rng.choice(allow)


CATEGORIES = {  # name: (weight, expected, hard_for_regex)
    "clean_compliant": (14, "COMPLIANT", False),
    "clean_breach_uptime": (10, "BREACH", False),
    "clean_breach_repair": (10, "BREACH", False),
    "noncritical_slow_ok": (12, "COMPLIANT", False),
    "contradiction_moot_breach": (12, "BREACH", False),
    "contradiction_moot_compliant": (10, "COMPLIANT", False),
    "contradiction_decisive": (14, "ESCALATED", False),
    "precedence_same_sentence": (8, None, False),
    "precedence_separate_clause": (8, None, True),
    "vague_both": (8, "ESCALATED", False),
    "vague_repair_uptime_breached": (8, "BREACH", False),
    "vague_repair_uptime_ok": (8, "ESCALATED", False),
    "no_telemetry": (8, "ESCALATED", False),
    "dirty_robust": (12, None, False),
    "dirty_flip": (10, "ESCALATED", False),
    "mostly_corrupt": (6, "ESCALATED", False),
    "words_and_days": (10, None, False),
    "planned_maintenance_excluded": (8, "COMPLIANT", False),
    "business_days": (6, "ESCALATED", False),
    "response_vs_resolution": (10, None, False),
}


def build_one(rng, nrng, cid: str, category: str, log_start: int):
    """Return (contract dict, telemetry DataFrame, truth dict)."""
    U = rng.choice([95.0, 96.0, 97.0, 97.5, 98.0, 98.5, 99.0, 99.5])
    R = rng.choice([4, 6, 8, 10, 12, 24])
    trap = rng.choice(["warranty", "dispute"]) if rng.random() < 0.2 else None
    style = "digits"
    scoped = True
    n = fleet_size(rng)
    sla: List[str] = []
    extra: List[str] = []
    expected = CATEGORIES[category][1]
    hard = CATEGORIES[category][2] or trap == "warranty"
    mean_up, crit_max, noncrit_max, planned_extra = min(U + rng.uniform(0.3, 0.9), 99.7), R * rng.uniform(0.4, 0.9), 0.0, None
    crit_cap = R * 0.9
    penalty_rate = rng.choice([250, 500, 1000, 2500]) if rng.random() < 0.6 else None
    thresholds = {"uptime": [U], "resolution_hours": [R]}
    unusable, harmless, decisive_fault, corrupt_ratio = 0, 0, None, 0.0
    no_telemetry = False

    def clear(x):  # keep the max away from the line by at least 1 hour
        return max(x, 0.5)

    if category == "clean_compliant":
        style = pick_style(rng)
    elif category == "clean_breach_uptime":
        mean_up = U - rng.uniform(0.6, 2.5)
    elif category == "clean_breach_repair":
        crit_max, crit_cap = R + rng.uniform(1.5, 12), R * 0.9
    elif category == "noncritical_slow_ok":
        noncrit_max = R * rng.uniform(2, 5)  # slow minor repairs; contract only covers critical events
    elif category.startswith("contradiction_"):
        R1 = R
        R2 = R1 * rng.choice([2, 3])
        thresholds["resolution_hours"] = [R1, R2]
        if category == "contradiction_moot_breach":
            crit_max, crit_cap = R2 + rng.uniform(2, 10), R1 * 0.9
        elif category == "contradiction_moot_compliant":
            crit_max, crit_cap = R1 * rng.uniform(0.3, 0.8), R1 * 0.9
        else:  # decisive: between the two windows
            crit_max, crit_cap = R1 + (R2 - R1) * rng.uniform(0.3, 0.7), R1 * 0.9
        first, second = (R1, R2) if rng.random() < 0.5 else (R2, R1)
        sla.append(repair_sentence(rng, first, "digits"))
        extra.append(f"ADDENDUM A. Due to regional parts shortages, the restoration window for Critical Events is "
                     f"set at {g(second)} hours for the remainder of the term.")
    elif category in ("precedence_same_sentence", "precedence_separate_clause"):
        R1 = R
        R2 = R1 * rng.choice([2, 3]) if rng.random() < 0.5 else max(2, R1 / 2)
        thresholds["resolution_hours"] = [R1, R2]
        lo, hi = sorted([R1, R2])
        crit_max, crit_cap = lo + (hi - lo) * rng.uniform(0.3, 0.7), lo * 0.9
        sla.append(repair_sentence(rng, R1, "digits"))
        if category == "precedence_same_sentence":
            extra.append(f"ADDENDUM B. Notwithstanding Section 3, the restoration time for Critical Events shall be {g(R2)} hours, "
                         "and this Addendum shall prevail over any conflicting provision.")
        else:
            extra.append(f"ADDENDUM B. The restoration time for Critical Events is {g(R2)} hours.")
            extra.append("ADDENDUM C. In the event of conflict between Section 3 and Addendum B, Addendum B shall prevail.")
    elif category == "vague_both":
        sla = ["The Provider shall keep fleet availability at optimal levels and shall address faults in a timely, "
               "industry-standard manner using best efforts."]
        thresholds = {"uptime": [], "resolution_hours": []}
    elif category.startswith("vague_repair_"):
        sla = [uptime_sentence(rng, U, "digits"), "Faults shall be addressed in a timely, industry-standard manner."]
        thresholds["resolution_hours"] = []
        if category == "vague_repair_uptime_breached":
            mean_up = U - rng.uniform(0.8, 2.5)
    elif category == "no_telemetry":
        no_telemetry = True
    elif category == "dirty_robust":
        style = pick_style(rng)
        unusable = max(1, int(n * rng.uniform(0.01, 0.04)))
        harmless = max(2, int(n * rng.uniform(0.02, 0.05)))
        if rng.random() < 0.5:
            mean_up = U - rng.uniform(0.6, 2.0)  # breach, robust to the dirt
    elif category == "dirty_flip":
        decisive_fault = R + rng.uniform(3, 10)
        unusable = max(1, int(n * 0.02))
    elif category == "mostly_corrupt":
        corrupt_ratio = rng.uniform(0.6, 1.2)
    elif category == "words_and_days":
        style = rng.choice(["words", "days"]) if R % 24 == 0 else "words"
        if rng.random() < 0.5:
            crit_max = R + rng.uniform(1.5, 12)
    elif category == "planned_maintenance_excluded":
        planned_extra = R + rng.uniform(5, 30)
        extra.append("ANNEX 1. Time attributable to Planned Maintenance approved in writing in advance is excluded from "
                     "all Critical Event restoration measurements.")
    elif category == "business_days":
        sla = [uptime_sentence(rng, U, "digits"),
               "Each Critical Event shall be restored within two business days of validated notification."]
        thresholds["resolution_hours"] = []
    elif category == "response_vs_resolution":
        if rng.random() < 0.5:
            crit_max = R + rng.uniform(1.5, 12)
        sla.append(uptime_sentence(rng, U, "digits"))
        sla.append(f"The Provider shall respond to each Critical Event within one hour and shall restore operational status within {g(R)} hours.")

    if not sla:
        sla = [uptime_sentence(rng, U, style), repair_sentence(rng, R, style, scoped)]
    elif category.startswith("contradiction_") or category.startswith("precedence_"):
        sla.insert(0, uptime_sentence(rng, U, "digits"))
    if category == "clean_breach_uptime" or (category == "dirty_robust" and mean_up < U) or category == "vague_repair_uptime_breached":
        if penalty_rate:
            sla.append(f"Service credits accrue at ${penalty_rate:,} per percentage point of uptime shortfall in the affected month.")
    if category in ("clean_breach_uptime",) and not penalty_rate:
        pass

    text = render_contract(rng, cid, sla, extra, trap)
    contract = {"contract_id": cid, "contract_text": text}

    # ---- telemetry
    if no_telemetry:
        df = pd.DataFrame(columns=COLUMNS)
        mean_up_real = None
    else:
        fl = Fleet(rng, nrng, cid, n)
        fl.build(mean_up, crit_max, noncrit_max, crit_cap, planned_extra)
        if unusable:
            fl.add_unusable(unusable)
        if harmless:
            fl.add_harmless_suspects(harmless)
        if decisive_fault:
            fl.add_decisive_fault_row(decisive_fault)
        if corrupt_ratio:
            fl.add_unusable(int(len(fl.rows) * corrupt_ratio) + 2)
        df = fl.frame(log_start)
        mean_up_real = float(np.mean(list(fl.truck_uptime.values())))

    # ---- truth (computed from the facts, not from the engine)
    penalty = None
    needs_review = None
    if category in ("precedence_same_sentence", "precedence_separate_clause"):
        governing = thresholds["resolution_hours"][1]
        expected = "BREACH" if crit_max > governing else "COMPLIANT"
    elif category == "dirty_robust":
        expected = "BREACH" if mean_up < U else "COMPLIANT"
    elif category in ("words_and_days", "response_vs_resolution"):
        expected = "BREACH" if crit_max > R else "COMPLIANT"
    if expected == "BREACH":
        if mean_up_real is not None and mean_up_real < U and penalty_rate:
            penalty = round((U - mean_up_real) * penalty_rate, 2)
            needs_review = False
        else:
            penalty, needs_review = None, True
    truth = {"contract_id": cid, "category": category, "expected_status": expected,
             "expected_penalty_usd": penalty, "expected_needs_commercial_review": needs_review,
             "hard_for_regex": hard, "has_distractor_clause": trap, "n_trucks": 0 if no_telemetry else n,
             "thresholds": thresholds, "penalty_rate_in_contract": bool(penalty_rate and "Service credits" in text)}
    return contract, df, truth


def generate(n_contracts: int, seed: int):
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    total_w = sum(w for w, _, _ in CATEGORIES.values())
    plan: List[str] = []
    for name, (w, _, _) in CATEGORIES.items():
        plan += [name] * max(2, round(n_contracts * w / total_w))
    rng.shuffle(plan)
    contracts, frames, truths = [], [], []
    log_id = 1
    for i, cat in enumerate(plan, start=1):
        cid = f"V2-{i:03d}"
        contract, df, truth = build_one(rng, nrng, cid, cat, log_id)
        log_id += len(df)
        contracts.append(contract)
        truths.append(truth)
        if len(df):
            frames.append(df)
    return contracts, pd.concat(frames, ignore_index=True), truths


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="data/v2")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    contracts, df, truths = generate(a.n, a.seed)
    (out / "contracts.json").write_text(json.dumps(contracts, indent=1), encoding="utf-8")
    (out / "ground_truth.json").write_text(json.dumps(truths, indent=1), encoding="utf-8")
    df.to_csv(out / "telemetry_logs.csv", index=False)
    print(f"{len(contracts)} contracts, {len(df):,} telemetry rows -> {out}")


if __name__ == "__main__":
    main()
