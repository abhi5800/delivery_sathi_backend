"""Telegram Bot API: webhook or long polling, and sending messages with tap-to-reply buttons."""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

from config import SSL_CONTEXT


def enabled():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN"))


def _call(method, payload=None, timeout=40):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{"ok": false}')


def bot_username():
    result = _call("getMe")
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected the bot token: {result.get('description')}")
    return result["result"]["username"]


def send_message(chat_id, text, options=None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if options:
        # Reply keyboard: tapping a button sends its label as a normal message.
        rows = [options[i:i + 2] for i in range(0, len(options), 2)]
        payload["reply_markup"] = {
            "keyboard": [[{"text": o} for o in row] for row in rows],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }
    else:
        payload["reply_markup"] = {"remove_keyboard": True}
    result = _call("sendMessage", payload, timeout=20)
    if not result.get("ok"):
        print(f"Telegram send to {chat_id} failed: {result.get('description')}")
    return result.get("ok", False)


def send_typing(chat_id):
    _call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)


def webhook_secret():
    """Secret Telegram echoes in X-Telegram-Bot-Api-Secret-Token, derived from the bot token (allowed chars: A-Za-z0-9_-)."""
    return hashlib.sha256(f"sathi-webhook:{os.environ['TELEGRAM_BOT_TOKEN']}".encode()).hexdigest()


def set_webhook(url):
    result = _call("setWebhook", {"url": url, "secret_token": webhook_secret(), "allowed_updates": ["message"]})
    if not result.get("ok"):
        raise RuntimeError(f"Telegram setWebhook failed: {result.get('description')}")


def dispatch(update, handle):
    """Call handle(chat_id, name, update_id, text) if the update is an incoming message."""
    msg = update.get("message") or {}
    text = _message_text(msg)
    if text is None:
        return
    sender = msg.get("from", {})
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or sender.get("username")
    handle(msg["chat"]["id"], name, update["update_id"], text)


def poll(handle):
    """Fetch updates forever (no public URL needed) and dispatch each incoming message."""
    _call("deleteWebhook")  # getUpdates does not work while a webhook is set
    offset = None
    while True:
        try:
            result = _call("getUpdates", {"timeout": 30, "offset": offset, "allowed_updates": ["message"]})
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"Telegram polling error: {e}; retrying")
            time.sleep(3)
            continue
        for update in result.get("result", []):
            offset = update["update_id"] + 1
            dispatch(update, handle)
        if not result.get("ok", True):
            print(f"Telegram polling error: {result.get('description')}")
            time.sleep(3)


def _message_text(msg):
    if "text" in msg:
        text = msg["text"]
        # /start (from the t.me link) opens the conversation like a greeting.
        return "Hi Sathi" if text.startswith("/start") else text
    if "location" in msg:
        loc = msg["location"]
        return f"[shared location {loc['latitude']},{loc['longitude']}]"
    if "contact" in msg:
        return f"[shared contact {msg['contact'].get('phone_number', '')}]"
    if msg.get("chat"):
        return "[sent a message Sathi can't read yet, like a photo or voice note]"
    return None
