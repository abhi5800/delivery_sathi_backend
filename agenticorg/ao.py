"""Minimal AgenticOrg client (stdlib only).

Reads AGENTICORG_EMAIL / AGENTICORG_PASSWORD (or AGENTICORG_API_KEY) from
../.env, logs in, and calls the API. Credentials and tokens are never printed.

Usage:
    python3 ao.py me
    python3 ao.py tools
    python3 ao.py call <tool_name> '<json arguments>'
    python3 ao.py get <api path>          e.g. /api/v1/agents
    python3 ao.py post <api path> '<json>' | @file.json
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env(ENV_PATH)
BASE_URL = os.getenv("AGENTICORG_BASE_URL", "https://agenticorg.hackathon.pinelabs.com").rstrip("/")
# python.org builds on macOS ship without root certs; fall back to the system bundle.
SSL_CONTEXT = ssl.create_default_context(cafile="/etc/ssl/cert.pem" if os.path.exists("/etc/ssl/cert.pem") else None)


def request(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
        # The hackathon deployment rejects the Bearer header on most routes but accepts the session cookie.
        req.add_header("Cookie", f"agenticorg_session={token}")
    try:
        with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")


class AgenticOrgError(Exception):
    pass


_token_cache = {"token": None, "at": 0.0}
TOKEN_REUSE_SECONDS = 50 * 60  # session tokens last 60 minutes


def get_token(fresh=False):
    if os.getenv("AGENTICORG_API_KEY"):
        return os.environ["AGENTICORG_API_KEY"]
    if not fresh and _token_cache["token"] and time.time() - _token_cache["at"] < TOKEN_REUSE_SECONDS:
        return _token_cache["token"]
    email, password = os.getenv("AGENTICORG_EMAIL"), os.getenv("AGENTICORG_PASSWORD")
    if not email or not password:
        raise AgenticOrgError(f"Set AGENTICORG_EMAIL and AGENTICORG_PASSWORD (or AGENTICORG_API_KEY) in {os.path.normpath(ENV_PATH)}")
    status, body = request("POST", "/api/v1/auth/login", {"email": email, "password": password})
    if status != 200:
        detail = body.get("detail") if isinstance(body, dict) else body
        raise AgenticOrgError(f"Login failed (HTTP {status}): {str(detail)[:200]}")
    _token_cache.update(token=body["access_token"], at=time.time())
    return body["access_token"]


def run_agent(agent_id, inputs):
    """Run an agent and return its output. Retries once on an expired session or a gateway error."""
    for attempt in range(2):
        status, body = request("POST", f"/api/v1/agents/{agent_id}/run", {"inputs": inputs}, token=get_token(fresh=attempt > 0))
        if status == 200 and isinstance(body, dict):
            return body.get("output")
        if status not in (401, 502, 503, 504):
            break
        time.sleep(2)
    raise AgenticOrgError(f"Agent run failed (HTTP {status}): {str(body)[:300]}")


def show(status, body):
    print(f"HTTP {status}")
    print(json.dumps(body, indent=2, ensure_ascii=False) if not isinstance(body, str) else body)


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    token = get_token()
    if cmd == "me":
        show(*request("GET", "/api/v1/auth/me", token=token))
    elif cmd == "tools":
        status, body = request("GET", "/api/v1/mcp/tools", token=token)
        tools = body.get("tools", []) if isinstance(body, dict) else []
        print(f"HTTP {status}: {len(tools)} tools")
        for t in tools:
            print("-", t["name"])
    elif cmd == "call":
        arguments = json.loads(args[1]) if len(args) > 1 else {}
        show(*request("POST", "/api/v1/mcp/call", {"name": args[0], "arguments": arguments}, token=token))
    elif cmd == "get":
        show(*request("GET", args[0], token=token))
    elif cmd == "post":
        body = args[1] if len(args) > 1 else "{}"
        if body.startswith("@"):
            with open(body[1:]) as f:
                body = f.read()
        show(*request("POST", args[0], json.loads(body), token=token))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except AgenticOrgError as e:
        sys.exit(str(e))
