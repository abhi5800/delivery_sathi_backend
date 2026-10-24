# Sathi seller onboarding bot

Sellers message Sathi on WhatsApp. This server keeps each seller's onboarding state in SQLite, asks the
**Sathi - Seller Onboarding** agent on AgenticOrg what to reply, and sends the reply back. Credentials
(shipping API user, payment gateway keys) are collected through one-time web forms, never in chat.

```
Seller on WhatsApp ─► Meta Cloud API ─► tunnel ─► server.py /webhook ─► AgenticOrg agent
                                                     │  SQLite: sellers, messages, forms, credentials
Seller ◄──────────── Meta Cloud API ◄────────────────┘  /form/<token>  /admin?key=...
```

## Try it in the terminal

```
python3 seller_bot/chat.py            # you play the seller; type "reset" to start over
```

## Go live on Telegram (pilot channel)

1. In Telegram, message **@BotFather**, send `/newbot`, choose a name and a username ending in `bot`.
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
3. `python3 seller_bot/server.py`. It prints the `https://t.me/<bot>` link to share with sellers.
   No tunnel needed: the server fetches messages from Telegram itself. Keep the Mac awake while sellers onboard.
4. Watch progress at `http://localhost:8080/admin?key=<ADMIN_KEY>`.

Secure forms stay off until `PUBLIC_URL` is an `https://` tunnel address; until then Sathi collects only
non-secret details and tells sellers the team will connect credentials on a call.

## Go live on WhatsApp (later)

1. **Meta app.** On developers.facebook.com, create an app (type Business) and add the WhatsApp product.
   *API Setup* shows a test number, a phone number ID and a temporary token.
   - The test number only messages up to 5 numbers you verify in that screen. That is enough for a demo.
   - To share with any seller, add your own business number there (one not already used in the WhatsApp app)
     and create a permanent token with a System User in Meta Business Settings. The temporary token expires in 24 hours.
2. **Fill in `.env`:** `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_DISPLAY_NUMBER`, `WHATSAPP_APP_SECRET`
   (app *Settings > Basic*). `WHATSAPP_VERIFY_TOKEN` and `ADMIN_KEY` are already generated.
3. **Expose the server over HTTPS.** Install cloudflared (Apple Silicon build: `cloudflared-darwin-arm64.tgz` from
   github.com/cloudflare/cloudflared/releases), then:
   ```
   cloudflared tunnel --url http://localhost:8080
   ```
   Put the printed `https://….trycloudflare.com` address in `.env` as `PUBLIC_URL`.
   Quick tunnels get a new address on each start; update `.env` and Meta when that happens.
4. **Start the server:** `python3 seller_bot/server.py`
5. **Connect the webhook.** In the Meta app: *WhatsApp > Configuration > Webhook*, callback URL `PUBLIC_URL/webhook`,
   verify token = `WHATSAPP_VERIFY_TOKEN`. Then subscribe to the **messages** field.
6. **Share** `https://wa.me/<WHATSAPP_DISPLAY_NUMBER>?text=Hi%20Sathi` with sellers.
   Watch progress at `PUBLIC_URL/admin?key=<ADMIN_KEY>`.

## Notes

- Sellers message first, so replies are free-form messages inside WhatsApp's 24-hour window; no templates needed.
  Messaging a seller first later (reminders, daily summary) will need approved templates.
- Changing the agent's behaviour: edit `agenticorg/agents/seller_onboarding.md` and PATCH it to the agent
  (see `agenticorg/ao.py`). The agent is in shadow mode, so AgenticOrg also queues each run for review.
- `sathi.db` stores credentials unencrypted. Fine for the hackathon; encrypt before onboarding real sellers.
