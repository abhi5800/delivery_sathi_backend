"""Buyer side of the demo: a mock courier moves COD orders along, and the Buyer Delivery agent talks to the buyer.

The buyer's replies come from the demo page (a stand-in for WhatsApp). Every Sathi message is decided by the
AgenticOrg agent; this module applies the agent's decision to the order and keeps the totals.
"""
import json
import random
import threading
from datetime import date

from config import BUYER_AGENT_ID  # first: puts agenticorg/ on the path for ao
import ao
import channels
import db

RETURN_COST = 200  # ₹ lost per return (freight both ways, repacking); industry estimate ₹180–250

COURIER_EVENTS = {
    "post_order": "New COD order placed",
    "out_for_delivery": "Courier: out for delivery today",
    "failed_attempt": "Courier: delivery attempt failed, marked 'customer not available'",
    "delivered": "Courier: delivered ✅",
}

SAMPLE_ORDERS = [
    {"buyer_name": "Priya Sharma", "buyer_language": "hinglish", "items": "2 cotton kurtis", "cod_amount": 1299,
     "address": "Flat 12, Shivaji Nagar", "landmark": "near Hanuman mandir", "city": "Pune", "pincode": "411005"},
    {"buyer_name": "Ramesh Yadav", "buyer_language": "hi", "items": "1 dupatta set", "cod_amount": 849,
     "address": "H.No 45, Sudama Nagar", "landmark": "", "city": "Indore", "pincode": "452009"},
    {"buyer_name": "Ankit Kumar", "buyer_language": "hinglish", "items": "1 printed kurta", "cod_amount": 999,
     "address": "Room 3, Shanti PG, Boring Road", "landmark": "", "city": "Patna", "pincode": "800001"},
    {"buyer_name": "Lakshmi R", "buyer_language": "en", "items": "3 kurtis", "cod_amount": 2199,
     "address": "12/4 3rd Street, Gandhipuram", "landmark": "", "city": "Coimbatore", "pincode": "641012"},
]

# Agent turns for one order run one at a time, in order.
_locks = {}
_locks_guard = threading.Lock()


def _lock_for(order_id):
    with _locks_guard:
        return _locks.setdefault(order_id, threading.Lock())


# ── Orders and messages ─────────────────────────────────────────────────────

def _get_order(order_id):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise KeyError(order_id)
    return dict(row)


def _update_order(order_id, **fields):
    fields["updated_at"] = db.now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE orders SET {sets} WHERE id = ?", (*fields.values(), order_id))


def _add_message(order_id, sender, body, meta=None):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO order_messages (order_id, sender, body, meta_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (order_id, sender, body, json.dumps(meta) if meta else None, db.now()),
        )


def _conversation(order_id, limit=10):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT sender, body FROM order_messages WHERE order_id = ? AND sender != 'system' ORDER BY id DESC LIMIT ?",
            (order_id, limit),
        ).fetchall()
    return [{"from": r["sender"], "text": r["body"]} for r in reversed(rows)]


def seed(seller_id=None):
    """Replace the demo orders with fresh sample orders for one seller."""
    if not seller_id:
        seller_id = "demo:kesar-kurtis"
        seller = db.get_or_create_seller(seller_id, "Demo seller")
        if not seller["business_name"]:
            db.save_seller(seller_id, {"business_name": "Kesar Kurtis", "brand_name": "Kesar Kurtis", "prepaid_discount": 0},
                           "live", "hinglish")
    seller = db.get_or_create_seller(seller_id)
    state = json.loads(seller["state_json"])
    brand = state.get("brand_name") or seller["business_name"] or "Kesar Kurtis"
    with db.connect() as conn:
        conn.execute("DELETE FROM order_messages")
        conn.execute("DELETE FROM orders")
        first = random.randint(1040, 9000)
        for i, sample in enumerate(SAMPLE_ORDERS):
            order_id = str(first + i)
            ts = db.now()
            conn.execute(
                """INSERT INTO orders (id, seller_id, brand, buyer_name, buyer_language, items, cod_amount, address,
                       landmark, city, pincode, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_id, seller_id, brand, sample["buyer_name"], sample["buyer_language"], sample["items"],
                 sample["cod_amount"], sample["address"], sample["landmark"], sample["city"], sample["pincode"], ts, ts),
            )


# ── Events from the mock courier and the buyer ──────────────────────────────

def courier_event(order_id, event):
    if event not in COURIER_EVENTS:
        raise ValueError(f"Unknown courier event: {event}")
    with _lock_for(order_id):
        order = _get_order(order_id)
        _add_message(order_id, "system", COURIER_EVENTS[event])
        if event == "delivered":
            _update_order(order_id, status="delivered", saved=int(order["had_failure"] or order["saved"]))
            return
        _update_order(order_id, moment=event, status=event,
                      had_failure=1 if event == "failed_attempt" else order["had_failure"])
        _agent_turn(_get_order(order_id), "", _conversation(order_id))


def buyer_reply(order_id, text):
    with _lock_for(order_id):
        order = _get_order(order_id)
        history = _conversation(order_id)
        _add_message(order_id, "buyer", text)
        _agent_turn(order, text, history)


def pay(order_id):
    """Mock UPI payment success: the order becomes prepaid and the courier's COD amount goes to ₹0."""
    with _lock_for(order_id):
        order = _get_order(order_id)
        if order["payment"] == "upi_paid":
            return
        _update_order(order_id, payment="upi_paid", status="prepaid", saved=int(order["had_failure"] or order["saved"]))
        _add_message(order_id, "system",
                     f"UPI payment of ₹{order['cod_amount']:,} received (mock) · courier updated: COD amount ₹0")
        thanks = ("Payment mil gaya, dhanyavaad! Rider ko ab cash dene ki zaroorat nahi."
                  if order["buyer_language"] in ("hi", "hinglish") else
                  "Payment received, thank you! You won't need to pay the rider anything.")
        _add_message(order_id, "sathi", thanks, {"source": "backend"})


def _agent_turn(order, buyer_message, history):
    seller = db.get_or_create_seller(order["seller_id"])
    state = json.loads(seller["state_json"])
    inputs = {
        "moment": order["moment"] or "post_order",
        "order": {
            "id": f"#{order['id']}", "brand": order["brand"], "items": order["items"], "cod_amount": order["cod_amount"],
            "address": ", ".join(filter(None, [order["address"], order["landmark"], order["city"]])),
            "pincode": order["pincode"], "buyer_name": order["buyer_name"], "buyer_language": order["buyer_language"],
        },
        "seller_rules": {
            "prepaid_discount": state.get("prepaid_discount", 0),
            "max_reattempts": state.get("max_reattempts", 2),
            "calling_hours": state.get("calling_hours", "9am to 8pm"),
        },
        "buyer_message": buyer_message,
        "history": history,
        "today": date.today().isoformat(),
    }
    try:
        output, meta = ao.run_agent_with_meta(BUYER_AGENT_ID, inputs)
        output = ao.parse_json_output(output)
    except (ao.AgenticOrgError, ValueError) as e:
        print(f"Buyer agent error for order {order['id']}: {e}")
        _add_message(order["id"], "system", "Sathi couldn't reach the AgenticOrg agent. Try again in a moment.")
        return
    _add_message(order["id"], "sathi", output.get("reply") or "…", {"source": "agenticorg", **meta,
                                                                      "intent": output.get("intent")})
    _apply_decision(order, output)


def _apply_decision(order, out):
    """Turn the agent's structured decision into order changes, courier actions and seller alerts."""
    order_id, intent = order["id"], out.get("intent")
    changes, notes, alert = {}, [], None

    if out.get("send_payment_link") or intent == "pay_upi":
        if order["payment"] != "upi_paid":
            changes["status"] = "upi_link_sent"
            _add_message(order_id, "system", f"UPI payment link sent for ₹{order['cod_amount']:,} (mock)",
                         {"pay_button": True})
    if intent == "confirmed":
        changes["status"] = "confirmed"
    if intent == "pay_cash":
        notes.append("Buyer will pay cash to the rider")

    address = out.get("address_update") if isinstance(out.get("address_update"), dict) else {}
    if any(address.values()):
        for key, column in (("line", "address"), ("landmark", "landmark"), ("pincode", "pincode")):
            if address.get(key):
                changes[column] = address[key]
        changes["status"] = "address_fixed"
        notes.append("Address updated: " + ", ".join(v for v in address.values() if v))

    if out.get("reattempt_date"):
        changes["reattempt_date"] = out["reattempt_date"]
        if changes.get("status") != "address_fixed":
            changes["status"] = "rescheduled"
        notes.append(f"Delivery rebooked for {out['reattempt_date']}")

    if intent == "cancel":
        changes["status"] = "cancelled"
        notes.append("Order cancelled before another wasted trip")

    if intent == "fake_attempt_claim":
        changes["status"] = "no_show_claim"
        changes["buyer_statement"] = out.get("buyer_statement") or ""
        notes.append(f"No-show claim recorded with the buyer's words: “{changes['buyer_statement']}”")
        alert = f"Buyer says the rider never came: “{changes['buyer_statement']}”. Logged as evidence for a courier dispute."

    if out.get("courier_action"):
        action = out["courier_action"].replace("_", " ")
        when = f" on {out['reattempt_date']}" if out.get("reattempt_date") else ""
        notes.append(f"Courier instruction sent (mock): {action}{when}")

    if out.get("seller_approval_needed"):
        changes["status"] = "needs_seller"
        notes.append(f"Waiting for seller approval: {out['seller_approval_needed']}")
        alert = f"Needs your approval: {out['seller_approval_needed']}"

    # A delivery counts as saved when Sathi fixes or rebooks it at the out-for-delivery or failed-attempt moment.
    if order["moment"] in ("out_for_delivery", "failed_attempt") and (
        out.get("reattempt_date") or any(address.values()) or out.get("courier_action") in ("reattempt", "update_address")
    ):
        changes["saved"] = 1

    for note in notes:
        _add_message(order_id, "system", note)
    if changes:
        _update_order(order_id, **changes)
    if alert and not order["seller_id"].startswith(("demo:", "console:")):
        channels.send(order["seller_id"], f"📦 Order #{order_id} · {order['buyer_name']}, {order['city']}\n{alert}")


# ── State for the demo page ─────────────────────────────────────────────────

def state():
    with db.connect() as conn:
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id").fetchall()]
        messages = [dict(r) for r in conn.execute("SELECT * FROM order_messages ORDER BY id").fetchall()]
    by_order = {}
    for m in messages:
        m["meta"] = json.loads(m.pop("meta_json") or "null")
        by_order.setdefault(m["order_id"], []).append(m)
    for o in orders:
        o["messages"] = by_order.get(o["id"], [])

    saved = [o for o in orders if o["saved"]]
    prepaid = [o for o in orders if o["payment"] == "upi_paid"]
    totals = {
        "orders": len(orders),
        "buyers_replied": sum(1 for o in orders if any(m["sender"] == "buyer" for m in o["messages"])),
        "failed_attempts": sum(o["had_failure"] for o in orders),
        "deliveries_saved": len(saved),
        "cod_value_saved": sum(o["cod_amount"] for o in saved),
        "returns_cost_avoided": len(saved) * RETURN_COST,
        "moved_to_upi": len(prepaid),
        "upi_amount": sum(o["cod_amount"] for o in prepaid),
        "no_show_claims": sum(1 for o in orders if o["buyer_statement"]),
    }
    sellers = [{"id": s["phone"], "name": s["business_name"] or s["profile_name"] or s["phone"], "step": s["step"]}
               for s in db.list_sellers()]
    return {"orders": orders, "totals": totals, "sellers": sellers, "return_cost": RETURN_COST}
