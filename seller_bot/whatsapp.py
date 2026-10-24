"""WhatsApp Cloud API: send messages, parse and verify webhooks.

With no WHATSAPP_TOKEN set, messages are printed to the console instead of sent.
"""
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request

from config import SSL_CONTEXT

GRAPH_URL = "https://graph.facebook.com/v21.0"


def enabled():
    return bool(os.getenv("WHATSAPP_TOKEN") and os.getenv("WHATSAPP_PHONE_NUMBER_ID"))


def send_text(to, body):
    if not enabled():
        print(f"\n[to {to}] {body}\n")
        return True
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body, "preview_url": True}}
    req = urllib.request.Request(
        f"{GRAPH_URL}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT):
            return True
    except urllib.error.HTTPError as e:
        print(f"WhatsApp send to {to} failed: HTTP {e.code} {e.read()[:300]!r}")
        return False


def signature_ok(raw_body, header):
    """Check Meta's X-Hub-Signature-256. Skipped when WHATSAPP_APP_SECRET is not set."""
    secret = os.getenv("WHATSAPP_APP_SECRET")
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def parse_messages(payload):
    """Yield (phone, profile_name, message_id, text) for each incoming message in a webhook payload."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                yield msg["from"], names.get(msg["from"]), msg["id"], _message_text(msg)


def _message_text(msg):
    kind = msg.get("type")
    if kind == "text":
        return msg["text"]["body"]
    if kind == "interactive":
        reply = msg["interactive"].get("button_reply") or msg["interactive"].get("list_reply") or {}
        return reply.get("title", "")
    if kind == "button":
        return msg["button"].get("text", "")
    if kind == "location":
        loc = msg["location"]
        return f"[shared location {loc.get('latitude')},{loc.get('longitude')} {loc.get('name', '')}]".strip()
    return f"[sent a {kind} message]"
