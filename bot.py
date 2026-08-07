"""
Fariborz Bot v5 - Python/Flask for Railway
Connects to Hermes API for AI chat, STT, TTS, Image Gen
"""

import os
import json
import time
import base64
import sqlite3
import threading
from flask import Flask, request, Response
import requests as http

# ─── Config ────────────────────────────────────────
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_URL = os.environ.get("HERMES_API_URL", "")
API_SECRET = os.environ.get("API_SECRET", "fariborz-hermes-2024")
PORT = int(os.environ.get("PORT", 8000))

TG = f"https://api.telegram.org/bot{TG_TOKEN}"
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bot.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
lock = threading.Lock()

# ─── DB ────────────────────────────────────────────
def init_db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, val TEXT)")
    c.commit(); c.close()

def kv_get(k):
    c = sqlite3.connect(DB); r = c.execute("SELECT val FROM kv WHERE key=?", (k,)).fetchone(); c.close()
    return r[0] if r else None

def kv_set(k, v):
    with lock:
        c = sqlite3.connect(DB); c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, str(v))); c.commit(); c.close()

def kv_del(k):
    with lock:
        c = sqlite3.connect(DB); c.execute("DELETE FROM kv WHERE key=?", (k,)); c.commit(); c.close()

init_db()

# ─── Telegram helpers ──────────────────────────────
def tg(method, **kw):
    try:
        r = http.post(f"{TG}/{method}", json=kw, timeout=15)
        return r.json()
    except Exception as e:
        print(f"TG error {method}: {e}")
        return {"ok": False}

def send_msg(cid, text, reply=None):
    p = {"chat_id": cid, "text": text}
    if reply: p["reply_to_message_id"] = reply
    return tg("sendMessage", **p)

def send_action(cid, act):
    return tg("sendChatAction", chat_id=cid, action=act)

def dl_file(file_id):
    try:
        info = tg("getFile", file_id=file_id)
        if not info.get("ok"): return None
        r = http.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{info['result']['file_path']}", timeout=30)
        return r.content
    except: return None

# ─── Hermes API ────────────────────────────────────
def hermes(endpoint, body):
    try:
        r = http.post(f"{API_URL}{endpoint}", json={"token": API_SECRET, **body}, timeout=120)
        d = r.json()
        return {"error": d["error"]} if d.get("error") else d
    except Exception as e:
        print(f"Hermes error {endpoint}: {e}")
        return {"error": "خطا در اتصال"}

# ─── Flask ─────────────────────────────────────────
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Fariborz Bot v5 ✅"

@app.route("/health", methods=["GET"])
def health():
    return '{"status":"ok"}'

@app.route("/setup", methods=["GET"])
def setup():
    r = tg("setWebhook", url=request.url_root.rstrip("/"))
    return f"Webhook: {r}"

@app.route("/", methods=["POST"])
def webhook():
    try:
        data = request.json
        if not data or "message" not in data:
            return "OK"

        msg = data["message"]
        cid = msg["chat"]["id"]
        uid = str(msg["from"]["id"])
        name = msg["from"].get("first_name", "")
        text = (msg.get("text") or msg.get("caption") or "").strip()
        mid = msg["message_id"]
        is_group = msg["chat"]["type"] != "private"
        has_photo = "photo" in msg
        has_voice = "voice" in msg

        # Rate limit 3s
        rl = kv_get(f"rl_{uid}")
        if rl and (time.time() - float(rl)) < 3:
            return "OK"
        kv_set(f"rl_{uid}", str(time.time()))

        # Group: only when mentioned/replied
        if is_group:
            rf = (msg.get("reply_to_message") or {}).get("from", {})
            bot_usernames = ["ShadowFariborz_bot", "nuxal_bot"]
            is_reply = rf.get("username") in bot_usernames
            mentions = ["فریبرز", "fariborz", "@fariborz_bot", "@nuxal_bot"]
            is_mentioned = any(m.lower() in text.lower() for m in mentions)
            if not is_reply and not is_mentioned:
                return "OK"
            for m in mentions:
                text = text.replace(m, "").strip()

        # Commands
        if text == "/start":
            send_msg(cid, f"سلام {name}! 👋\nمن فریبرز هستم.\n\n💬 متن بفرست\n🎤 ویس بفرست\n🎨 بگو \"عکس بساز\"\n🔊 بگو \"ویس بفرست\"", mid)
            return "OK"
        if text == "/help":
            send_msg(cid, "📋 راهنما:\n💬 هر متنی → جواب\n🎤 ویس → میفهمم\n🎨 \"عکس بساز ...\" → عکس\n🔊 \"ویس بفرست ...\" → صدا", mid)
            return "OK"

        # Voice → STT
        if has_voice:
            send_action(cid, "record_voice")
            data = dl_file(msg["voice"]["file_id"])
            if not data:
                send_msg(cid, "❌ خطا در دانلود صدا", mid)
                return "OK"
            b64 = base64.b64encode(data).decode()
            stt = hermes("/speech-to-text", {"audio_base64": b64})
            if not stt.get("text"):
                send_msg(cid, "❌ نتونستم صدا رو بفهمم", mid)
                return "OK"
            send_msg(cid, f'🎤 شنیدم: "{stt["text"]}"', mid)
            text = stt["text"]
            has_voice = False

        # Image gen
        if text and (text.startswith("عکس") or text.startswith("تصویر") or text.startswith("/generate-image")):
            prompt = text
            for p in ["عکس بساز", "عکس بکن", "تصویر بساز", "تصویر بکن", "/generate-image"]:
                prompt = prompt.replace(p, "").strip()
            if not prompt:
                send_msg(cid, "🎨 موضوع عکس رو بگو!", mid)
                return "OK"
            send_action(cid, "upload_photo")
            res = hermes("/generate-image", {"prompt": prompt})
            if not res.get("image_base64"):
                send_msg(cid, "❌ نتونستم عکس بسازم", mid)
                return "OK"
            img = base64.b64decode(res["image_base64"])
            try:
                http.post(f"{TG}/sendPhoto", files={"photo": ("img.jpg", img, "image/jpeg")}, data={"chat_id": str(cid), "caption": f"🎨 {prompt}"}, timeout=30)
            except:
                send_msg(cid, "❌ خطا در ارسال عکس", mid)
            return "OK"

        # TTS
        if text and (text.startswith("ویس") or text.startswith("صدا") or text.startswith("/voice")):
            tts_text = text
            for p in ["ویس بفرست", "ویس بده", "صدا بفرست", "صدا بده", "/voice"]:
                tts_text = tts_text.replace(p, "").strip()
            if not tts_text:
                send_msg(cid, "🔊 متن رو بگو!\nمثال: /voice سلام دنیا", mid)
                return "OK"
            send_action(cid, "record_voice")
            res = hermes("/text-to-speech", {"text": tts_text})
            if not res.get("audio_base64"):
                send_msg(cid, "❌ نتونستم صدا بسازم", mid)
                return "OK"
            aud = base64.b64decode(res["audio_base64"])
            try:
                http.post(f"{TG}/sendVoice", files={"voice": ("voice.mp3", aud, "audio/mpeg")}, data={"chat_id": str(cid)}, timeout=30)
            except:
                send_msg(cid, "❌ خطا در ارسال صدا", mid)
            return "OK"

        # Chat with Hermes
        send_action(cid, "typing")

        # Photo
        image_b64 = None
        if has_photo:
            pd = dl_file(msg["photo"][-1]["file_id"])
            if pd:
                image_b64 = base64.b64encode(pd).decode()

        # Reply chain
        ctx = []
        cur = msg
        for _ in range(5):
            rm = cur.get("reply_to_message")
            if not rm: break
            rn = rm.get("from", {}).get("first_name", "?")
            rt = (rm.get("text") or rm.get("caption") or "")[:300]
            if "photo" in rm and not image_b64:
                pd = dl_file(rm["photo"][-1]["file_id"])
                if pd: image_b64 = base64.b64encode(pd).decode()
                ctx.insert(0, f"[عکس از {rn}]: {rt}")
            elif "voice" in rm:
                ctx.insert(0, f"[ویس از {rn}]: {rt or '(پیام صوتی)'}")
            else:
                ctx.insert(0, f"[پیام {rn}]: {rt}")
            cur = rm

        full_msg = text or "این عکس رو توضیح بده"
        if ctx:
            full_msg = "\n".join(ctx) + f"\n[{name}]: {full_msg}"

        body = {"message": full_msg, "user_id": uid, "user_name": name}
        if image_b64:
            body["image_base64"] = image_b64

        res = hermes("/chat", body)
        if res.get("error"):
            send_msg(cid, f"⚠️ {res['error']}", mid)
        else:
            send_msg(cid, res.get("response", "پاسخی دریافت نشد"), mid)

        return "OK"
    except Exception as e:
        print(f"Error: {e}")
        return "OK"

if __name__ == "__main__":
    print(f"🤖 Fariborz Bot starting on port {PORT}")
    print(f"   API: {API_URL}")
    app.run(host="0.0.0.0", port=PORT)
