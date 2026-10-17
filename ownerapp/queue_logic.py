"""Which CAs a refresh should queue, and how a result is merged.

A CA already Queued or Checking is not added again. A run older than four
minutes is stale, so a restart can put Checking rows back to Queued.
An Error result does not wipe a previous amount.
"""

from __future__ import annotations

STALE_MS = 4 * 60 * 1000
MODES = ("all", "unpaid", "error", "selected", "one")


def bill_state(status: str) -> str:
    if str(status or "").strip() in ("Paid", "Unpaid"):
        return "Done"
    return "Error"


def is_unpaid(row: dict) -> bool:
    return str(row.get("status") or "").strip() == "Unpaid"


def is_error(row: dict) -> bool:
    status = str(row.get("status") or "").strip()
    check = str(row.get("check_state") or "").strip()
    return status.startswith("Error") or check == "Error"


def select_cas(rows: list[dict], mode: str, options: dict | None = None) -> dict:
    options = options or {}
    mode = str(mode or "").strip().lower()
    if mode not in MODES:
        return {"ok": False, "error": "Unknown refresh.", "cas": []}
    ordered = []
    by_ca = {}
    for row in rows:
        ca = row.get("ca_no") or ""
        if not ca or ca in by_ca:
            continue
        if row.get("active") is False and mode != "one":
            continue
        item = {
            "ca_no": ca,
            "name": row.get("name") or "",
            "tenant_id": row.get("tenant_id"),
            "status": row.get("status") or "",
            "check_state": row.get("check_state") or "",
        }
        by_ca[ca] = item
        ordered.append(item)
    if mode == "all":
        picked = ordered
    elif mode == "unpaid":
        picked = [row for row in ordered if is_unpaid(row)]
    elif mode == "error":
        picked = [row for row in ordered if is_error(row)]
    elif mode == "one":
        from ownerapp.rent_logic import normalize_ca

        one = normalize_ca(options.get("ca_no"))
        if not one:
            return {"ok": False, "error": "Type an 11-digit CA number.", "cas": []}
        if one not in by_ca and options.get("allow_unknown"):
            picked = [{"ca_no": one, "name": "", "tenant_id": None, "status": "", "check_state": ""}]
        elif one not in by_ca:
            return {"ok": False, "error": "That CA is not on an active tenant.", "cas": []}
        else:
            picked = [by_ca[one]]
    else:
        wanted = []
        seen = set()
        for raw in options.get("selected") or []:
            ca = str(raw or "").strip()
            if ca in by_ca and ca not in seen:
                wanted.append(by_ca[ca])
                seen.add(ca)
        if not wanted:
            return {"ok": False, "error": "Select at least one tenant with a CA number.", "cas": []}
        picked = wanted
    return {"ok": True, "cas": picked}


def dedupe_jobs(open_cas: list[str], incoming: list[dict]) -> dict:
    open_set = set(open_cas)
    add = []
    skipped = []
    seen = set()
    for row in incoming:
        ca = row["ca_no"]
        if ca in open_set or ca in seen:
            skipped.append(ca)
            continue
        seen.add(ca)
        add.append(row)
    return {"add": add, "skipped": skipped}


def lock_is_stale(heartbeat_ms: int | None, now_ms: int, stale_ms: int = STALE_MS) -> bool:
    if not heartbeat_ms:
        return True
    return now_ms - int(heartbeat_ms) >= stale_ms


def merge_bill(previous: dict | None, result: dict) -> dict:
    """Keep the last good amounts when this check is an Error."""
    previous = previous or {}
    status = str(result.get("status") or "Error")
    merged = {
        "ca_no": result.get("ca_no") or previous.get("ca_no"),
        "status": status,
        "note": result.get("note") or "",
        "checked_at": result.get("checked_at") or "",
        "check_state": bill_state(status),
    }
    if status in ("Paid", "Unpaid"):
        merged.update(
            {
                "amount": result.get("amount"),
                "due_date": result.get("due_date") or "",
                "exact_bill": result.get("exact_bill"),
                "bill_no": result.get("bill_no") or "",
                "bill_date": result.get("bill_date") or "",
                "consumer_name": result.get("consumer_name") or "",
                "last_payment": result.get("last_payment") if result.get("last_payment") not in ("", None) else None,
                "last_paid_on": result.get("last_paid_on") or "",
            }
        )
    else:
        merged.update(
            {
                "amount": previous.get("amount"),
                "due_date": previous.get("due_date") or "",
                "exact_bill": previous.get("exact_bill"),
                "bill_no": previous.get("bill_no") or "",
                "bill_date": previous.get("bill_date") or "",
                "consumer_name": previous.get("consumer_name") or "",
                "last_payment": previous.get("last_payment"),
                "last_paid_on": previous.get("last_paid_on") or "",
                "status": status if status.startswith("Error") else f"Error: {status}",
                "check_state": "Error",
            }
        )
    return merged
