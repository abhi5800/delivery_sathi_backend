"""Route replies to the seller's channel. Seller ids carry the channel: "tg:<chat id>", "wa:<phone>", "console:<name>"."""
import telegram
import whatsapp


def send(seller_id, text, options=None):
    channel, _, address = seller_id.partition(":")
    if channel == "tg":
        return telegram.send_message(address, text, options)
    if options:
        text += "\n\n" + " / ".join(options)  # no buttons on these channels yet
    if channel == "wa":
        return whatsapp.send_text(address, text)
    print(f"\n[sathi] {text}\n")
    return True


def typing(seller_id):
    channel, _, address = seller_id.partition(":")
    if channel == "tg":
        telegram.send_typing(address)
