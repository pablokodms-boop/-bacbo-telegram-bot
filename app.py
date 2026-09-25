
import os
import sqlite3
from datetime import datetime, timezone
from collections import Counter

import requests
from flask import Flask, request, jsonify
import psycopg2

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ENDPOINT_TOKEN = os.environ.get("BACBO_ENDPOINT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

VALID_RESULTS = {"PLAYER", "BANKER", "TIE"}

def is_postgres():
    return DATABASE_URL.startswith(("postgres://", "postgresql://"))

def get_conn():
    if is_postgres():
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect("bacbo.db")
    conn.row_factory = sqlite3.Row
    return conn

def p():
    return "%s" if is_postgres() else "?"

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    if is_postgres():
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rounds(
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                result TEXT NOT NULL,
                round_key TEXT,
                created_at TIMESTAMPTZ NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats(
                chat_id BIGINT PRIMARY KEY,
                last_seen TIMESTAMPTZ NOT NULL
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rounds(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                result TEXT NOT NULL,
                round_key TEXT,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats(
                chat_id INTEGER PRIMARY KEY,
                last_seen TEXT NOT NULL
            )
        """)
    conn.commit()
    cur.close()
    conn.close()

def normalize_result(v):
    if v is None:
        return None
    s = str(v).strip()
    m = {
        "PLAYER":"PLAYER", "BANKER":"BANKER", "TIE":"TIE",
        "PlayerWon":"PLAYER", "BankerWon":"BANKER", "Tie1to14":"TIE",
        "player":"PLAYER", "banker":"BANKER", "tie":"TIE",
    }
    return m.get(s)

def telegram(method, payload):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload, timeout=20
    )
    r.raise_for_status()
    return r.json()

def send(chat_id, text):
    return telegram("sendMessage", {"chat_id": chat_id, "text": text})

def touch_chat(chat_id):
    now = datetime.now(timezone.utc)
    conn = get_conn()
    cur = conn.cursor()
    if is_postgres():
        cur.execute("""
            INSERT INTO chats(chat_id,last_seen) VALUES(%s,%s)
            ON CONFLICT(chat_id) DO UPDATE SET last_seen=EXCLUDED.last_seen
        """, (chat_id, now))
    else:
        cur.execute("""
            INSERT INTO chats(chat_id,last_seen) VALUES(?,?)
            ON CONFLICT(chat_id) DO UPDATE SET last_seen=excluded.last_seen
        """, (chat_id, now.isoformat()))
    conn.commit()
    cur.close()
    conn.close()

def latest_chat():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM chats ORDER BY last_seen DESC LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return None if not row else int(row[0])

def add_round(chat_id, result, round_key=None):
    conn = get_conn()
    cur = conn.cursor()
    if round_key:
        cur.execute(
            f"SELECT id FROM rounds WHERE chat_id={p()} AND round_key={p()} LIMIT 1",
            (chat_id, round_key)
        )
        if cur.fetchone():
            cur.close()
            conn.close()
            return False

    now = datetime.now(timezone.utc)
    cur.execute(
        f"INSERT INTO rounds(chat_id,result,round_key,created_at) VALUES({p()},{p()},{p()},{p()})",
        (chat_id, result, round_key, now if is_postgres() else now.isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()
    return True

def recent(chat_id, n):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT result FROM rounds WHERE chat_id={p()} ORDER BY id DESC LIMIT {int(n)}",
        (chat_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]

def stats(chat_id):
    out = ["📊 Bac Bo statistics"]
    for n in (20, 50, 100):
        items = recent(chat_id, n)
        c = Counter(items)
        total = len(items)
        def pct(k):
            return 0.0 if total == 0 else c[k] / total * 100
        out.append(
            f"\nLast {n} rounds ({total} recorded):\n"
            f"Player 🔵: {pct('PLAYER'):.1f}% ({c['PLAYER']})\n"
            f"Banker 🔴: {pct('BANKER'):.1f}% ({c['BANKER']})\n"
            f"Tie 🟢: {pct('TIE'):.1f}% ({c['TIE']})"
        )
    return "\n".join(out)

def delete_last(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT id,result FROM rounds WHERE chat_id={p()} ORDER BY id DESC LIMIT 1",
        (chat_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return None
    cur.execute(f"DELETE FROM rounds WHERE id={p()}", (row[0],))
    conn.commit()
    cur.close()
    conn.close()
    return row[1]

def history(chat_id):
    items = list(reversed(recent(chat_id, 20)))
    if not items:
        return "No results recorded."
    icons = {"PLAYER":"🔵","BANKER":"🔴","TIE":"🟢"}
    return "🕘 Last results:\n" + " ".join(f"{icons[x]} {x}" for x in items)

def handle_text(chat_id, text):
    touch_chat(chat_id)
    text = (text or "").strip()
    cmd = text.split()[0].lower() if text else ""

    if cmd in ("/start", "/help"):
        send(chat_id,
             "🎲 Bac Bo bot ready.\n\n"
             "/add player\n/add banker\n/add tie\n/stats\n/history\n/undo")
    elif cmd == "/stats":
        send(chat_id, stats(chat_id))
    elif cmd == "/history":
        send(chat_id, history(chat_id))
    elif cmd == "/undo":
        removed = delete_last(chat_id)
        send(chat_id, f"↩️ Removed: {removed}" if removed else "Nothing to remove.")
    elif cmd == "/add":
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            send(chat_id, "Usage: /add player, /add banker, or /add tie")
            return
        result = normalize_result(parts[1])
        if result not in VALID_RESULTS:
            send(chat_id, "Usage: /add player, /add banker, or /add tie")
            return
        add_round(chat_id, result)
        send(chat_id, f"✅ {result} saved.\n\n{stats(chat_id)}")
    else:
        send(chat_id, "Unknown command. Use /help.")

@app.get("/")
def health():
    return jsonify({"ok": True, "service": "bacbo-telegram-bot"})

@app.post("/telegram")
def telegram_webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return jsonify({"ok": False}), 401
    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = message.get("text")
    if chat_id and text:
        handle_text(int(chat_id), text)
    return jsonify({"ok": True})

@app.post("/bacbo-result")
def bacbo_result():
    if request.headers.get("Authorization", "") != f"Bearer {ENDPOINT_TOKEN}":
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    result = normalize_result(body.get("result"))
    round_key = body.get("round_id") or body.get("roundKey")

    if result not in VALID_RESULTS:
        return jsonify({"ok": False, "error": "invalid result"}), 400

    chat_id = latest_chat()
    if chat_id is None:
        return jsonify({"ok": False, "error": "send /start to the Telegram bot first"}), 409

    saved = add_round(chat_id, result, str(round_key) if round_key else None)
    if saved:
        send(chat_id, f"🎲 Live result: {result}\n\n{stats(chat_id)}")

    return jsonify({"ok": True, "saved": saved, "result": result})

def configure_webhook():
    if BOT_TOKEN and WEBHOOK_SECRET and PUBLIC_BASE_URL:
        try:
            telegram("setWebhook", {
                "url": f"{PUBLIC_BASE_URL}/telegram",
                "secret_token": WEBHOOK_SECRET,
                "drop_pending_updates": False
            })
        except Exception as e:
            print("Webhook setup warning:", e)

init_db()
configure_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
