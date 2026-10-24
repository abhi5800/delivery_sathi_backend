"""Chat with the onboarding agent in the terminal, as if you were a seller on Telegram or WhatsApp.

    python3 seller_bot/chat.py [name]

Uses the same database and logic as the server; replies print here. Type "reset" to start over, Ctrl+C to quit.
"""
import sys

import db
import onboarding

if __name__ == "__main__":
    seller_id = f"console:{sys.argv[1] if len(sys.argv) > 1 else 'test'}"
    db.init()
    print(f"Chatting as {seller_id}. Type 'reset' to start over.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text:
            onboarding.handle_message(seller_id, text, profile_name="Test Seller")
