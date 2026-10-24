"""Seller onboarding server.

    python3 seller_bot/server.py

Routes:
    GET  /                 landing page
    POST /telegram/webhook incoming Telegram messages (webhook mode, e.g. on Render)
    GET  /webhook          Meta webhook verification
    POST /webhook          incoming WhatsApp messages
    GET  /form/<token>     secure form for credentials
    POST /form/<token>
    GET  /admin?key=...    onboarding progress for every seller
    GET  /health
"""
import hmac
import html
import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import ADMIN_KEY, PORT, PUBLIC_URL, SECURE_FORMS, TELEGRAM_MODE, VERIFY_TOKEN
import db
import onboarding
import telegram
import whatsapp

LANDING_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")

PAGE_STYLE = """
:root{--bg:#f6f7f5;--card:#fff;--text:#1d2420;--muted:#5d6b63;--line:#dfe4e0;--accent:#128c4a}
@media (prefers-color-scheme:dark){:root{--bg:#121614;--card:#1b211e;--text:#e8ede9;--muted:#9aa8a0;--line:#2c3530;--accent:#3ccf7d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:560px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 8px}p{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
label{display:block;font-weight:600;margin:14px 0 6px}input,select{width:100%;padding:10px 12px;border:1px solid var(--line);
border-radius:8px;background:var(--bg);color:var(--text);font:inherit}button{margin-top:20px;width:100%;padding:12px;border:0;
border-radius:8px;background:var(--accent);color:#fff;font:600 16px system-ui}small{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}
.wide{max-width:1100px}.flag{color:#c0392b;font-weight:600}
"""


def page(title, body, wide=False):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{PAGE_STYLE}</style></head><body><main class="{'wide' if wide else ''}">{body}</main></body></html>"""


def form_page(form, error=""):
    spec = onboarding.FORMS[form["kind"]]
    fields = []
    for f in spec["fields"]:
        req = " required" if f.get("required") else ""
        if f.get("type") == "select":
            opts = "".join(f"<option>{html.escape(o)}</option>" for o in f["options"])
            control = f'<select name="{f["name"]}"{req}><option value="">Choose…</option>{opts}</select>'
        else:
            control = (f'<input name="{f["name"]}" type="{f.get("type", "text")}" '
                       f'placeholder="{html.escape(f.get("placeholder", ""))}" autocomplete="off"{req}>')
        fields.append(f'<label>{html.escape(f["label"])}</label>{control}')
    err = f'<p class="flag">{html.escape(error)}</p>' if error else ""
    return page(spec["title"], f"""<h1>{html.escape(spec['title'])}</h1><p>{html.escape(spec['intro'])}</p>{err}
<form class="card" method="post">{''.join(fields)}<button type="submit">Submit</button></form>
<p style="margin-top:16px"><small>Delivery Sathi. This link works once and is only for you.</small></p>""")


def admin_page():
    rows = []
    for s in db.list_sellers():
        state = json.loads(s["state_json"])
        details = ", ".join(f"{k}: {v}" for k, v in state.items() if k not in ("step", "language"))
        rows.append(f"""<tr><td>{html.escape(s['phone'])}<br><small>{html.escape(s['profile_name'] or '')}</small></td>
<td>{html.escape(s['business_name'] or '')}<br><small>{html.escape(s['seller_name'] or '')}</small></td>
<td>{html.escape(s['step'])}{' <span class="flag">needs human</span>' if s['needs_human'] else ''}</td>
<td>{html.escape(s['language'] or '')}</td><td><small>{html.escape(details)}</small></td>
<td><small>{html.escape(s['updated_at'])}</small></td></tr>""")
    table = "".join(rows) or '<tr><td colspan="6">No sellers yet.</td></tr>'
    return page("Seller onboarding", f"""<h1>Seller onboarding</h1><p>{len(rows)} sellers</p><div class="card" style="overflow-x:auto">
<table><tr><th>Seller (channel:id)</th><th>Business</th><th>Step</th><th>Lang</th><th>Collected</th><th>Updated</th></tr>{table}</table></div>""", wide=True)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status, body, content_type="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _form(self):
        token = self.path.split("?")[0].removeprefix("/form/")
        form = db.get_form(token)
        if not form:
            self._reply(404, page("Not found", "<h1>Link not found</h1><p>Ask Sathi on WhatsApp for a new link.</p>"))
            return None, None
        if form["submitted_at"]:
            self._reply(410, page("Already submitted", "<h1>Already submitted</h1><p>Thanks! You can close this page and go back to WhatsApp.</p>"))
            return None, None
        return token, form

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        if url.path == "/health":
            self._reply(200, "ok", "text/plain")
        elif url.path in ("/", "/index.html"):
            with open(LANDING_PAGE, "rb") as f:
                self._reply(200, f.read())
        elif url.path == "/webhook":
            if VERIFY_TOKEN and query.get("hub.mode") == "subscribe" and query.get("hub.verify_token") == VERIFY_TOKEN:
                self._reply(200, query.get("hub.challenge", ""), "text/plain")
            else:
                self._reply(403, "forbidden", "text/plain")
        elif url.path.startswith("/form/"):
            token, form = self._form()
            if form:
                self._reply(200, form_page(form))
        elif url.path == "/admin":
            if ADMIN_KEY and query.get("key") == ADMIN_KEY:
                self._reply(200, admin_page())
            else:
                self._reply(403, "forbidden", "text/plain")
        else:
            self._reply(404, "not found", "text/plain")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        path = self.path.split("?")[0]
        if path == "/telegram/webhook":
            if not telegram.enabled() or not hmac.compare_digest(
                self.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), telegram.webhook_secret()
            ):
                self._reply(401, "bad secret", "text/plain")
                return
            self._reply(200, "ok", "text/plain")  # acknowledge fast; the agent reply is sent separately
            try:
                telegram.dispatch(json.loads(raw), handle_telegram)
            except (ValueError, KeyError):
                pass
        elif path == "/webhook":
            if not whatsapp.signature_ok(raw, self.headers.get("X-Hub-Signature-256")):
                self._reply(401, "bad signature", "text/plain")
                return
            self._reply(200, "ok", "text/plain")  # acknowledge fast; Meta retries slow webhooks
            try:
                payload = json.loads(raw)
            except ValueError:
                return
            for phone, name, msg_id, text in whatsapp.parse_messages(payload):
                threading.Thread(target=onboarding.handle_message, args=(f"wa:{phone}", text, name, msg_id), daemon=True).start()
        elif path.startswith("/form/"):
            token, form = self._form()
            if not form:
                return
            fields = dict(urllib.parse.parse_qsl(raw.decode()))
            missing = [f["label"] for f in onboarding.FORMS[form["kind"]]["fields"] if f.get("required") and not fields.get(f["name"], "").strip()]
            if missing:
                self._reply(400, form_page(form, "Please fill in: " + ", ".join(missing)))
                return
            onboarding.complete_form(token, form, fields)
            self._reply(200, page("Thank you", "<h1>Thank you! ✅</h1><p>Saved securely. Go back to WhatsApp, Sathi will continue there.</p>"))
        else:
            self._reply(404, "not found", "text/plain")

    def log_message(self, fmt, *args):
        # Keep form tokens and admin keys out of the console log.
        print(f"{self.command} {self.path.split('?')[0].split('/form/')[0] or '/form'} {args[1] if len(args) > 1 else ''}")


def handle_telegram(chat_id, name, update_id, text):
    threading.Thread(
        target=onboarding.handle_message, args=(f"tg:{chat_id}", text, name, f"tg-update:{update_id}"), daemon=True
    ).start()


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # show logs immediately when run in the background
    db.init()
    if telegram.enabled():
        username = telegram.bot_username()
        if TELEGRAM_MODE == "webhook":
            telegram.set_webhook(f"{PUBLIC_URL}/telegram/webhook")
            print(f"Telegram webhook: {PUBLIC_URL}/telegram/webhook")
        else:
            threading.Thread(target=telegram.poll, args=(handle_telegram,), daemon=True).start()
            print("Telegram: polling for messages")
        print(f"Telegram: share https://t.me/{username} with sellers")
    if whatsapp.enabled():
        print(f"WhatsApp: webhook at {PUBLIC_URL}/webhook")
        if not VERIFY_TOKEN:
            print("Warning: WHATSAPP_VERIFY_TOKEN is not set; Meta webhook verification will fail.")
    if not (telegram.enabled() or whatsapp.enabled()):
        print("No channel configured: set TELEGRAM_BOT_TOKEN (or the WHATSAPP_ settings) in .env")
    print(f"Secure forms: {'on' if SECURE_FORMS else 'off'}")
    print(f"Landing page: {PUBLIC_URL}/  Admin page: {PUBLIC_URL}/admin?key=<ADMIN_KEY>")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
