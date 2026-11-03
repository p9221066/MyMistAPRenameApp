#!/usr/bin/env python3
"""
Rename Juniper Mist access points from a CSV file using the mistapi library.

The CSV must have these columns (header names are case-insensitive):
    Name            new name to give the AP
    MAC Address     AP MAC (any format: aa:bb:cc..., aa-bb-..., aabb.cc..., aabbcc...)
    Serial Number   AP serial, used to double-check the match (optional per row)

For each row the script finds the AP in the org inventory by MAC address and
renames it. It is a dry-run by default: it prints the plan only. Pass --apply
to push the new names to Mist.

Credentials come from a .env file (default: ./.env) or environment variables:
    MIST_HOST=api.mist.com          # or api.eu.mist.com, api.gc1.mist.com, ...
    MIST_APITOKEN=xxxxxxxxxxxxxxxx

Examples:
    python mist_ap_rename.py aps.csv                       # preview
    python mist_ap_rename.py aps.csv --report plan.csv     # preview + save plan
    python mist_ap_rename.py aps.csv --apply               # rename
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import Counter

import mistapi
from mistapi.api.v1.orgs import inventory as org_inventory
from mistapi.api.v1.self import self as mist_self
from mistapi.api.v1.sites import devices as site_devices

MAX_NAME_LEN = 64
COL_NAME, COL_MAC, COL_SERIAL = "name", "mac address", "serial number"

log = logging.getLogger("ap_rename")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def normalize_mac(mac: str) -> str:
    return re.sub(r"[^0-9a-f]", "", (mac or "").lower())


def check(resp, what: str):
    if resp.status_code != 200:
        raise RuntimeError(f"{what} failed: HTTP {resp.status_code} {resp.data}")
    return resp


def read_csv(path: str) -> list[dict]:
    """Return rows as dicts with keys: row, name, mac, serial."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = {h.strip().lower(): h for h in (reader.fieldnames or []) if h}
        missing = [c for c in (COL_NAME, COL_MAC) if c not in headers]
        if missing:
            sys.exit(f"{path}: missing column(s): {', '.join(missing)}. "
                     f"Found: {', '.join(reader.fieldnames or [])}")
        rows = []
        for i, raw in enumerate(reader, start=2):  # row 1 is the header
            name = (raw.get(headers[COL_NAME]) or "").strip()
            mac_raw = (raw.get(headers[COL_MAC]) or "").strip()
            serial = (raw.get(headers.get(COL_SERIAL, ""), "") or "").strip()
            if not name and not mac_raw:
                continue  # skip blank lines
            rows.append({"row": i, "name": name, "mac_raw": mac_raw,
                         "mac": normalize_mac(mac_raw), "serial": serial})
    return rows


# --------------------------------------------------------------------------- #
# Mist API calls
# --------------------------------------------------------------------------- #
def resolve_org_id(session, org_id: str | None) -> str:
    if org_id:
        return org_id
    me = check(mist_self.getSelf(session), "getSelf").data
    orgs = {p["org_id"]: p.get("name", "") for p in me.get("privileges", []) if p.get("org_id")}
    if len(orgs) == 1:
        oid = next(iter(orgs))
        log.info("Using org %s (%s)", orgs[oid], oid)
        return oid
    listing = "\n".join(f"  {oid}  {name}" for oid, name in orgs.items())
    sys.exit(f"Token has access to {len(orgs)} orgs; pass --org-id. Available:\n{listing}")


def get_ap_inventory(session, org_id: str) -> dict[str, dict]:
    """All APs in the org inventory, keyed by normalized MAC."""
    resp = check(org_inventory.getOrgInventory(session, org_id, type="ap", limit=1000), "getOrgInventory")
    items = mistapi.get_all(session, resp)
    log.info("Org inventory: %d AP(s)", len(items))
    return {normalize_mac(d.get("mac", "")): d for d in items}


# --------------------------------------------------------------------------- #
# Planning / applying
# --------------------------------------------------------------------------- #
def build_plan(rows: list[dict], inventory: dict[str, dict], check_serial: bool) -> list[dict]:
    mac_counts = Counter(r["mac"] for r in rows)
    name_counts = Counter(r["name"].lower() for r in rows)

    plan = []
    for r in rows:
        dev = inventory.get(r["mac"])
        old_name = (dev or {}).get("name") or ""
        mist_serial = (dev or {}).get("serial") or ""
        item = {
            "row": r["row"], "mac": r["mac_raw"], "serial": r["serial"],
            "mist_serial": mist_serial, "old_name": old_name, "new_name": r["name"],
            "site_id": (dev or {}).get("site_id") or "", "device_id": (dev or {}).get("id") or "",
        }

        if len(r["mac"]) != 12:
            status = "error: invalid MAC"
        elif not r["name"]:
            status = "error: empty name"
        elif len(r["name"]) > MAX_NAME_LEN:
            status = f"error: name longer than {MAX_NAME_LEN}"
        elif mac_counts[r["mac"]] > 1:
            status = "error: MAC repeated in CSV"
        elif name_counts[r["name"].lower()] > 1:
            status = "error: name repeated in CSV"
        elif not dev:
            status = "error: MAC not found in Mist"
        elif check_serial and r["serial"] and r["serial"].upper() != mist_serial.upper():
            status = "error: serial mismatch"
        elif not item["site_id"]:
            status = "error: AP not assigned to a site"
        elif r["name"] == old_name:
            status = "unchanged"
        else:
            status = "rename"
        item["status"] = status
        plan.append(item)
    return plan


def print_plan(plan: list[dict]) -> None:
    cols = [("ROW", "row"), ("MAC", "mac"), ("CURRENT NAME", "old_name"),
            ("NEW NAME", "new_name"), ("STATUS", "status")]
    widths = [max(len(h), *(len(str(p[k])) for p in plan)) for h, k in cols]
    print("  ".join(h.ljust(w) for (h, _), w in zip(cols, widths)))
    for p in plan:
        print("  ".join(str(p[k]).ljust(w) for (_, k), w in zip(cols, widths)))


def write_report(plan: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(plan[0].keys()))
        writer.writeheader()
        writer.writerows(plan)
    log.info("Report written to %s", path)


def apply_plan(session, plan: list[dict]) -> None:
    for p in plan:
        if p["status"] != "rename":
            continue
        resp = site_devices.updateSiteDevice(session, p["site_id"], p["device_id"], {"name": p["new_name"]})
        if resp.status_code == 200:
            p["status"] = "renamed"
            log.info("Renamed %s: '%s' -> '%s'", p["mac"], p["old_name"], p["new_name"])
        else:
            p["status"] = f"failed: HTTP {resp.status_code}"
            log.error("Failed %s: HTTP %s %s", p["mac"], resp.status_code, resp.data)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Rename Mist APs from a CSV (Name, MAC Address, Serial Number).",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("csv_file", help="CSV with columns Name, MAC Address, Serial Number")
    ap.add_argument("--env-file", default=".env", help="File with MIST_HOST / MIST_APITOKEN (default: .env)")
    ap.add_argument("--org-id", help="Mist org ID (auto-detected if the token has a single org)")
    ap.add_argument("--no-serial-check", action="store_true",
                    help="Don't require the CSV serial to match the serial in Mist")
    ap.add_argument("--report", help="Write the plan/result to this CSV file")
    ap.add_argument("--apply", action="store_true", help="Actually rename the APs (otherwise dry-run)")
    ap.add_argument("--yes", action="store_true", help="Skip the confirmation prompt with --apply")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    rows = read_csv(args.csv_file)
    if not rows:
        sys.exit(f"{args.csv_file}: no data rows.")
    log.info("CSV: %d row(s)", len(rows))

    session = mistapi.APISession(env_file=args.env_file, console_log_level=30)
    session.login()
    org_id = resolve_org_id(session, args.org_id)

    plan = build_plan(rows, get_ap_inventory(session, org_id), check_serial=not args.no_serial_check)

    print()
    print_plan(plan)
    counts = Counter(p["status"].split(":")[0] for p in plan)
    print(f"\nTotal: {len(plan)}  rename: {counts['rename']}  unchanged: {counts['unchanged']}  "
          f"errors: {counts['error']} (error rows are skipped)")

    if args.apply and counts["rename"]:
        if not args.yes:
            answer = input(f"\nRename {counts['rename']} AP(s) in Mist? Type 'yes' to continue: ")
            if answer.strip().lower() != "yes":
                print("Aborted.")
                return 1
        apply_plan(session, plan)
    elif not args.apply:
        print("\nDry-run only. Re-run with --apply to make these changes.")

    if args.report:
        write_report(plan, args.report)
    failed = any(p["status"].startswith(("failed", "error")) for p in plan)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
