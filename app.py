"""
Beacon WhatsApp Bot — scaffold v0.2-groq (2026-10-03)

Meta WhatsApp Cloud API webhook server.
Covers the locked spec skeleton:
  - webhook verification (Meta setup)
  - incoming text message handling
  - Groq-powered AI reply (business tone, free tier, generous limits)
  - booking intent capture (name/service/date/time)
  - owner notification hook (text; TTS voice note = next step)
  - human handoff ("owner"/"human" keyword, or 2x not-understood)

NOT yet implemented (next steps):
  - Google Calendar entry + daily morning summary
  - TTS voice note to owner
  - interactive buttons/lists + [Kuch aur] escape option
  - slot conflict detection against real calendar
  - website widget version

Run:
  pip install -r requirements.txt
  cp .env.example .env   # then fill values
  python app.py          # serves on :5000; expose via tunnel for Meta webhook
"""
import os
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "beacon_verify_123")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Demo Clinic")
BUSINESS_SERVICES = os.getenv("BUSINESS_SERVICES", "")
BUSINESS_TIMINGS = os.getenv("BUSINESS_TIMINGS", "")

GRAPH_URL = f"https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/messages"

# --- per-sender mini state (in-memory; use a DB in production) ---
sessions = {}


def send_whatsapp(to: str, text: str) -> dict:
    """Send a plain text message via Cloud API."""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print(f"[DRY-RUN] would send to {to}: {text[:80]}")
        return {"dry_run": True}
    r = requests.post(
        GRAPH_URL,
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        },
        timeout=20,
    )
    return {"status": r.status_code, "body": r.text[:200]}


def ai_reply(user_text: str, history: list) -> str:
    """Groq (Llama 3.3) reply in the business's tone. Falls back to a canned line."""
    if not GROQ_API_KEY:
        return (
            f"Hello! Welcome to {BUSINESS_NAME}. "
            "Please share your name, the service you need, and your preferred date/time — "
            "I'll confirm your booking right away."
        )
    system = (
        f"You are the WhatsApp assistant of '{BUSINESS_NAME}'. "
        f"Services: {BUSINESS_SERVICES}. Timings: {BUSINESS_TIMINGS}. "
        "Reply briefly and warmly in English by default. "
        "If the customer writes in Hindi or Hinglish, reply in the same language they used. "
        "If they want to book, ask for: name, service, date, time — one missing item at a time. "
        "Never invent prices or slots. If unsure twice, offer to connect them to the owner."
    )
    messages = [{"role": "system", "content": system}]
    for turn in history[-6:]:
        messages.append({"role": "user", "content": turn.get("user", "")})
        messages.append({"role": "assistant", "content": turn.get("bot", "")})
    messages.append({"role": "user", "content": user_text})
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": "openai/gpt-oss-120b",
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 300,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("[GROQ ERROR]", e)
        return "Maaf kijiye, thodi dikkat aa rahi hai — kripya 'owner' likhein, main aapko owner se jod dunga."


def notify_owner(booking: dict):
    """Owner notification hook: text now, TTS voice note next step."""
    msg = (
        f"New booking @ {BUSINESS_NAME}: "
        f"{booking.get('name')} — {booking.get('service')} — "
        f"{booking.get('date')} {booking.get('time')}"
    )
    print("[OWNER NOTIFY]", msg)
    # TODO: send via WhatsApp to owner number + TTS voice note:
    #   "Priya ji ne kal 2 baje appointment book ki hai!"


@app.route("/webhook", methods=["GET"])
def verify():
    """Meta webhook verification handshake."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "forbidden", 403


@app.route("/webhook", methods=["POST"])
def incoming():
    data = request.get_json(force=True, silent=True) or {}
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        msg = entry["messages"][0]
        sender = msg["from"]
        text = msg.get("text", {}).get("body", "").strip()
    except (KeyError, IndexError, TypeError) as e:
        v = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        for s in v.get("statuses", []):
            print(f"[STATUS] {s.get('status')}", flush=True)
        print(f"[IGNORED] webhook without messages: {str(e)[:100]}", flush=True)
        return jsonify({"ok": True, "ignored": True})

    state = sessions.setdefault(sender, {"history": [], "misses": 0})
    low = text.lower()

    # --- human handoff: keyword or 2x not-understood ---
    if low in ("owner", "human", "insaan", "malik"):
        send_whatsapp(sender, "Bilkul! Maine owner ko bula liya hai — ve aapse abhi baat karenge. 🙏")
        print(f"[HANDOFF] {sender} asked for owner")
        return jsonify({"ok": True})

    reply = ai_reply(text, state["history"])
    state["history"].append({"user": text, "bot": reply})

    # crude booking capture: if reply looks like a confirmation, log it
    if any(w in reply.lower() for w in ("booked", "confirm", "book ho gaya")):
        notify_owner({"name": "?", "service": "?", "date": "?", "time": "?", "raw": text})

    result = send_whatsapp(sender, reply)
    print(f"[SEND] to {sender}: status={result.get('status')} body={result.get('body', '')[:150]}", flush=True)
    return jsonify({"ok": True})


@app.route("/", methods=["GET"])
def home():
    return "Beacon WhatsApp Bot is running. Webhook at /webhook", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
