"""Office PC snapshots: what computer someone is on, its IP, network link and SSD state.

A small helper on each office computer answers HF Hub's page (on that computer only)
with a signed snapshot. The browser relays it here; the signature proves it came from
an enrolled computer and wasn't edited on the way.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta

MAX_SNAPSHOT_AGE = 120          # seconds a signed snapshot stays valid
VISIT_EVERY = timedelta(minutes=10)

# Status thresholds (green / amber / red)
DISK_WARN_PCT = 80
DISK_BAD_PCT = 90
WEAR_WARN_PCT = 80
GIGABIT = 1000


class SnapshotError(Exception):
    pass


def new_secret() -> str:
    return secrets.token_hex(32)


def new_setup_code() -> str:
    # 8 characters, no look-alikes (0/O, 1/I)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def verify(conn, payload_b64: str, sig: str) -> tuple:
    """(workstation row, snapshot dict) for a genuine, fresh snapshot — else SnapshotError."""
    try:
        raw = base64.b64decode(payload_b64, validate=True)
        data = json.loads(raw)
        device_id = int(data.get("device_id") or 0)
        ts = float(data.get("ts") or 0)
        sig = str(sig or "").lower().encode("ascii")
    except (ValueError, TypeError, AttributeError, UnicodeEncodeError):
        raise SnapshotError("unreadable snapshot")
    ws = conn.execute("SELECT * FROM workstations WHERE id=? AND revoked=0", (device_id,)).fetchone()
    if not ws:
        raise SnapshotError("unknown computer")
    expected = hmac.new(ws["secret"].encode(), raw, hashlib.sha256).hexdigest().encode()
    if not hmac.compare_digest(expected, sig):
        raise SnapshotError("bad signature")
    if abs(time.time() - ts) > MAX_SNAPSHOT_AGE:
        raise SnapshotError("snapshot expired")
    return ws, data


def evaluate(data: dict, expected_link: str = "cable") -> dict:
    """Turn raw helper readings into what people see: status + plain-language warnings."""
    warnings = []           # (severity, message)
    link_type = data.get("link_type") or "none"
    mbps = data.get("link_mbps")
    try:
        mbps = int(mbps) if mbps not in (None, "") else None
    except (TypeError, ValueError):
        mbps = None

    if link_type == "none":
        warnings.append(("bad", "Not connected to the network."))
    elif link_type == "wifi" and expected_link != "wifi":
        warnings.append(("warn", "On Wi-Fi instead of the network cable — plug the cable in for full speed."))
    elif link_type == "cable" and mbps and mbps < GIGABIT:
        warnings.append(("warn", f"Network is running at {mbps} Mbps instead of 1 Gbps — the cable or wall port may be faulty. Tell IT."))

    disks = []
    for d in data.get("disks") or []:
        name = str(d.get("name") or "Drive")[:40]
        used = d.get("used_pct")
        health = (d.get("health") or "").strip()
        wear = d.get("wear_pct")
        disk = {"name": name, "used_pct": used, "health": health or None, "wear_pct": wear,
                "temp_c": d.get("temp_c"), "state": "ok"}
        if isinstance(used, (int, float)):
            if used >= DISK_BAD_PCT:
                disk["state"] = "bad"
                warnings.append(("bad", f"{name} is {used:.0f}% full — the PC may slow down or fail to save files. Clear space or tell IT today."))
            elif used >= DISK_WARN_PCT:
                disk["state"] = "warn"
                warnings.append(("warn", f"{name} is over {DISK_WARN_PCT}% full — clear old exports."))
        if health and health.lower() not in ("healthy", "verified", "ok"):
            disk["state"] = "bad"
            warnings.append(("bad", f"{name} health: {health} — back up your work and tell IT."))
        elif isinstance(wear, (int, float)) and wear >= WEAR_WARN_PCT:
            if disk["state"] == "ok":
                disk["state"] = "warn"
            warnings.append(("warn", f"{name} is {wear:.0f}% worn — plan a replacement."))
        disks.append(disk)

    severities = {w[0] for w in warnings}
    status = "bad" if "bad" in severities else "warn" if "warn" in severities else "ok"
    return {
        "hostname": str(data.get("hostname") or "")[:80],
        "platform": data.get("platform"),
        "ip": str(data.get("ip") or "")[:45] or None,
        "link_type": link_type,
        "link_mbps": mbps,
        "disks": disks,
        "status": status,
        "warnings": [{"severity": s, "message": m} for s, m in warnings],
    }


def record(conn, ws, view: dict, emp_id, attendance_id, kind: str, now_pht: datetime) -> dict:
    """Save the snapshot (time_in/time_out always; page visits at most every 10 minutes)
    and refresh the computer's latest state. Returns the view enriched with names."""
    name = ws["label"] or view["hostname"] or f"Computer {ws['id']}"
    view = {**view, "workstation_id": ws["id"], "name": name, "expected_link": ws["expected_link"],
            "taken_at": now_pht.strftime("%Y-%m-%d %H:%M:%S")}
    stamp = view["taken_at"]
    conn.execute(
        """UPDATE workstations SET hostname=?, platform=COALESCE(?, platform), last_seen=?,
           last_emp_id=?, last_status=?, last_snapshot=? WHERE id=?""",
        (view["hostname"] or ws["hostname"], view["platform"], stamp, emp_id, view["status"],
         json.dumps(view), ws["id"]))
    if kind == "visit":
        last = conn.execute(
            """SELECT taken_at, status FROM workstation_snapshots
               WHERE workstation_id=? AND emp_id IS ? ORDER BY id DESC LIMIT 1""",
            (ws["id"], emp_id)).fetchone()
        if last and last["status"] == view["status"]:
            try:
                if now_pht - datetime.strptime(last["taken_at"], "%Y-%m-%d %H:%M:%S") < VISIT_EVERY:
                    return view
            except ValueError:
                pass
    conn.execute(
        """INSERT INTO workstation_snapshots
           (workstation_id, emp_id, attendance_id, kind, taken_at, ip, link_type, link_mbps, disks, status, warnings)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (ws["id"], emp_id, attendance_id, kind, stamp, view["ip"], view["link_type"], view["link_mbps"],
         json.dumps(view["disks"]), view["status"], json.dumps(view["warnings"])))
    return view


def tv_line(view: dict) -> dict:
    """Compact form for the TV card's bottom line."""
    fullest = max((d for d in view.get("disks") or [] if isinstance(d.get("used_pct"), (int, float))),
                  key=lambda d: d["used_pct"], default=None)
    if view.get("link_type") == "wifi":
        net = "Wi-Fi"
    elif view.get("link_type") == "cable":
        net = f"{view['link_mbps'] // 1000} Gbps" if (view.get("link_mbps") or 0) >= 1000 else f"{view.get('link_mbps') or '?'} Mbps"
    else:
        net = "Offline"
    worst = next((w["message"] for w in view.get("warnings") or [] if w["severity"] == "bad"), None) or \
        next((w["message"] for w in view.get("warnings") or []), None)
    return {
        "name": view.get("name"), "ip": view.get("ip"), "status": view.get("status"), "net": net,
        "net_warn": (view.get("link_type") == "none"
                     or (view.get("link_type") == "wifi" and view.get("expected_link") != "wifi")
                     or (view.get("link_type") == "cable" and (view.get("link_mbps") or 0) < GIGABIT)),
        "ssd": f"SSD {fullest['used_pct']:.0f}%" if fullest else None,
        "ssd_state": fullest["state"] if fullest else None,
        "warning": worst,
    }
