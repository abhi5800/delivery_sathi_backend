"""Shared settings: loads ../.env (when present) and exposes the AgenticOrg client."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agenticorg"))

import ao  # noqa: E402  (loads ../.env on import)

SSL_CONTEXT = ao.SSL_CONTEXT

with open(os.path.join(ROOT, "agenticorg", "agent_ids.json")) as f:
    SELLER_AGENT_ID = os.getenv("SELLER_AGENT_ID") or json.load(f)["sathi_seller_onboarding"]

# Render sets PORT and RENDER_EXTERNAL_URL; locally we fall back to .env values.
PORT = int(os.getenv("PORT") or os.getenv("SELLER_BOT_PORT", "8080"))
# Public HTTPS address of this server; used for webhooks and form links sent to sellers.
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or f"http://localhost:{PORT}").rstrip("/")
# "webhook" needs an https PUBLIC_URL (Render); "poll" works anywhere, including a laptop.
TELEGRAM_MODE = os.getenv("TELEGRAM_MODE") or ("webhook" if PUBLIC_URL.startswith("https://") else "poll")
# Credential forms store secrets unencrypted, so they stay off unless explicitly enabled.
SECURE_FORMS = os.getenv("SECURE_FORMS", "off") == "on" and PUBLIC_URL.startswith("https://")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
