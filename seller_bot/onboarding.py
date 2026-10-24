"""One seller message in, one reply out. The AgenticOrg agent decides the reply; we keep the state."""
import json
import re
import secrets
import threading
from datetime import date

from config import PUBLIC_URL, SECURE_FORMS, SELLER_AGENT_ID  # first: puts agenticorg/ on the path for ao
import ao
import channels
import db

RESET_WORDS = {"reset", "restart", "start over", "/reset"}
FALLBACK_REPLY = "Sorry, I'm having a small technical issue. Please send your message again in a minute."

# Per-seller locks so two quick messages from one seller are handled in order.
_locks = {}
_locks_guard = threading.Lock()


def _lock_for(seller_id):
    with _locks_guard:
        return _locks.setdefault(seller_id, threading.Lock())


def handle_message(seller_id, text, profile_name=None, message_id=None):
    """Process one incoming seller message and send the reply. Safe to call from a worker thread.

    seller_id carries the channel, e.g. "tg:123456" or "wa:919876543210" (see channels.py).
    """
    with _lock_for(seller_id):
        if not db.log_message(seller_id, "in", text, message_id):
            return None  # duplicate delivery from the channel
        if text.strip().lower() in RESET_WORDS:
            db.reset_seller(seller_id)
            return _send(seller_id, "Done, starting fresh. Say hi to begin!")

        channels.typing(seller_id)
        seller = db.get_or_create_seller(seller_id, profile_name)
        state = json.loads(seller["state_json"])
        state.setdefault("step", seller["step"])
        if profile_name:
            state.setdefault("profile_name", profile_name)

        try:
            output = _parse_output(ao.run_agent(SELLER_AGENT_ID, {
                "seller_message": text,
                "state": state,
                "history": db.recent_messages(seller_id)[:-1],  # the current message is passed separately
                "today": date.today().isoformat(),
                "channel": seller_id.partition(":")[0],
                "secure_forms_available": SECURE_FORMS,
            }))
        except (ao.AgenticOrgError, ValueError) as e:
            print(f"Agent error for {seller_id}: {e}")
            return _send(seller_id, FALLBACK_REPLY)

        state.update({k: v for k, v in (output.get("updates") or {}).items() if v not in (None, "")})
        step = output.get("step") or state["step"]
        language = output.get("language") or seller["language"]
        state.update(step=step, language=language)
        db.save_seller(seller_id, state, step, language, needs_human=bool(output.get("handoff_to_human")))

        reply = output.get("reply") or FALLBACK_REPLY
        form_kind = output.get("needs_secure_form")
        if SECURE_FORMS and form_kind in FORMS and not state.get(f"{form_kind}_done"):
            token = secrets.token_urlsafe(16)
            db.create_form(token, seller_id, form_kind)
            reply += f"\n\n🔒 {FORMS[form_kind]['title']}: {PUBLIC_URL}/form/{token}"
        options = [str(o)[:40] for o in (output.get("options") or []) if o][:6]
        return _send(seller_id, reply, options)


def _send(seller_id, body, options=None):
    channels.send(seller_id, body, options)
    db.log_message(seller_id, "out", body)
    return body


def _parse_output(output):
    """The agent returns a JSON object, sometimes as a string or wrapped in a code fence."""
    if isinstance(output, dict):
        if "reply" in output:
            return output
        output = output.get("text") or output.get("content") or json.dumps(output)
    if isinstance(output, str):
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    raise ValueError(f"Unexpected agent output: {str(output)[:200]}")


def complete_form(token, form, fields):
    """Store a submitted form, mark it done in the seller's state, and let the agent continue."""
    spec = FORMS[form["kind"]]
    data = {f["name"]: fields.get(f["name"], "").strip() for f in spec["fields"]}
    db.submit_form(token, form["phone"], form["kind"], data)

    seller = db.get_or_create_seller(form["phone"])
    state = json.loads(seller["state_json"])
    state[f"{form['kind']}_done"] = True
    # Keep the non-secret answers in the conversation state; secrets stay only in seller_credentials.
    state.update({f["name"]: data[f["name"]] for f in spec["fields"] if not f.get("secret") and data[f["name"]]})
    db.save_seller(form["phone"], state, seller["step"], seller["language"], bool(seller["needs_human"]))
    threading.Thread(
        target=handle_message, args=(form["phone"], f"[I submitted the secure form: {form['kind']}]"), daemon=True
    ).start()


FORMS = {
    "shopify_install": {
        "title": "Connect your Shopify store",
        "intro": "Share your store address. We'll send you the Delivery Sathi app install link on WhatsApp.",
        "fields": [
            {"name": "store_url", "label": "Shopify store address", "placeholder": "yourstore.myshopify.com", "required": True},
        ],
    },
    "woocommerce_plugin": {
        "title": "Connect your WooCommerce store",
        "intro": "Share your website address. We'll send the plugin and setup steps on WhatsApp.",
        "fields": [
            {"name": "store_url", "label": "Website address", "placeholder": "https://yourstore.in", "required": True},
        ],
    },
    "shipping_credentials": {
        "title": "Connect your shipping account",
        "intro": "This lets Sathi see when parcels are out for delivery or a delivery fails. "
                 "On Shiprocket, create a separate API user under Settings > API and use that here, not your main login.",
        "fields": [
            {"name": "shipping_provider", "label": "Shipping partner", "type": "select", "required": True,
             "options": ["Shiprocket", "Delhivery", "Shipway", "iThink Logistics", "NimbusPost", "Other", "I'll paste AWB numbers"]},
            {"name": "api_email", "label": "API user email (or account ID)", "placeholder": "api@yourbrand.in"},
            {"name": "api_secret", "label": "API password or token", "type": "password", "secret": True},
        ],
    },
    "payment_credentials": {
        "title": "Set up UPI payments",
        "intro": "Buyers who choose to pay by UPI pay straight into your account. A UPI ID is enough to start.",
        "fields": [
            {"name": "payment_gateway", "label": "Payment gateway", "type": "select", "required": True,
             "options": ["Pine Labs Plural", "Razorpay", "Cashfree", "Other", "No gateway, use my UPI ID"]},
            {"name": "upi_id", "label": "UPI ID", "placeholder": "brand@okhdfcbank"},
            {"name": "gateway_key_id", "label": "Gateway key ID / client ID (optional)"},
            {"name": "gateway_secret", "label": "Gateway secret (optional)", "type": "password", "secret": True},
        ],
    },
}
