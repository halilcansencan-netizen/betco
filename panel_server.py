import os
import json
import random
import asyncio
from threading import Thread
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters
)

# --- Load .env variables ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# --- Flask App ---
app = Flask(__name__)

# --- File paths ---
USERNAMES_FILE = "usernames.json"
WINNERS_FILE = "winners.json"
CODES_FILE = "codes.json"
USED_CODES_FILE = "used_codes.json"

# --- Load/save helpers ---
def load_json(file, default):
    if not os.path.exists(file):
        return default
    try:
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- Global State ---
usernames = load_json(USERNAMES_FILE, {})
participants = set()
awaiting_username = set()
raffle_active = False
winners = []
winner_count = 1

# --- Code management ---
def load_codes():
    return load_json(CODES_FILE, [])

def save_codes(codes):
    save_json(CODES_FILE, codes)

def get_random_code():
    codes = load_codes()
    if not codes:
        return None
    code = random.choice(codes)
    codes.remove(code)
    save_codes(codes)

    used = load_json(USED_CODES_FILE, [])
    used.append(code)
    save_json(USED_CODES_FILE, used)

    return code

# --- Flask Routes ---
@app.route("/")
def index():
    return send_file("index.html")

@app.route("/set-codes", methods=["POST"])
def set_codes():
    data = request.get_json()
    codes = data.get("codes", [])
    if not isinstance(codes, list):
        return "Kod listesi geçersiz", 400
    save_codes(codes)
    return f"{len(codes)} kod kaydedildi."

@app.route("/usernames", methods=["GET"])
def get_usernames():
    return jsonify(usernames)

@app.route("/delete-username/<int:uid>", methods=["DELETE"])
def delete_username(uid):
    uid_str = str(uid)
    if uid_str in usernames:
        del usernames[uid_str]
        save_json(USERNAMES_FILE, usernames)
        return "Kullanıcı adı silindi."
    return "Kullanıcı bulunamadı.", 404

@app.route("/winners", methods=["GET"])
def get_winners():
    return jsonify(load_json(WINNERS_FILE, []))

# --- Telegram Logic ---
def is_owner(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == OWNER_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("👤 Kullanıcı Adını Tanımla")]]
    await update.message.reply_text(
        "Merhaba! Kullanıcı adını tanımlamayı unutma 🎯",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def handle_username_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        awaiting_username.add(user.id)
        await update.message.reply_text("📢 Lütfen site içindeki kullanıcı adını yaz.")

async def capture_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in awaiting_username:
        return
    username_input = update.message.text.strip()
    for uid_str, uname in usernames.items():
        if uname.lower() == username_input.lower() and int(uid_str) != user.id:
            await update.message.reply_text("❌ Bu kullanıcı adı başka biri tarafından alınmış.")
            return
    usernames[str(user.id)] = username_input
    save_json(USERNAMES_FILE, usernames)
    awaiting_username.remove(user.id)
    await update.message.reply_text(f"✅ Kullanıcı adı kaydedildi: {username_input}")

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    if not raffle_active:
        return await query.answer("Aktif çekiliş yok.", show_alert=True)
    if user.id in participants:
        return await query.answer("Zaten katıldın 🎉", show_alert=True)
    participants.add(user.id)
    await query.answer("Katıldın 🎉")

# --- Auto Raffle Logic ---
async def auto_raffle_loop():
    global raffle_active, participants, winners
    print("[LOOP] Çekiliş döngüsü başladı.")
    app_telegram = Application.builder().token(BOT_TOKEN).build()
    bot = app_telegram.bot
    bot_username = (await bot.get_me()).username

    while True:
        await asyncio.sleep(60)  # 5 dakika bekle
        code = get_random_code()
        if not code:
            print("Kod kalmadı. Çekiliş yapılmadı.")
            continue

        # Başlat
        raffle_active = True
        participants.clear()
        winners.clear()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Katıl", callback_data="join")],
            [InlineKeyboardButton("👤 Kullanıcı Adı Belirle", url=f"https://t.me/{bot_username}")]
        ])

        await bot.send_message(
            chat_id=TARGET_CHANNEL_ID,
            text="🎯 Yeni çekiliş başladı! Katılmak için butona tıkla.\n1 dakika sürecek!",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

        await asyncio.sleep(60)  # 1 dakika bekle

        # Bitir
        if not participants:
            raffle_active = False
            continue

        selected = random.sample(list(participants), 1)
        winners = []

        for uid in selected:
            mention = f"<a href='tg://user?id={uid}'>Kullanıcı</a>"
            site_user = usernames.get(str(uid))
            winners.append({"telegram": mention, "site": site_user, "code": code})

            try:
                await bot.send_message(
                    chat_id=uid,
                    text=f"🎉 Tebrikler! Ödül kodun: <code>{code}</code>",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass

        raffle_active = False
        save_json(WINNERS_FILE, winners)

        try:
            result_text = "\n".join(
                 f"🏆 @{w['username'] or 'kullanici'} (Site: {w['site'] or 'Tanımsız'})"
                     for w in winners
)

          
            await bot.send_message(
                chat_id=TARGET_CHANNEL_ID,
                text=f"🎉 Çekiliş bitti!\n\n{result_text}",
                parse_mode=ParseMode.HTML
            )
        except:
            pass

# --- Start Flask & Bot ---
def run_flask():
    app.run(port=5000)
async def start_auto_raffle(app: Application):
    app.create_task(auto_raffle_loop())

def run_bot():
    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(start_auto_raffle)  # burada başlatılıyor
        .build()
    )

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(join, pattern="^join$"))
    telegram_app.add_handler(MessageHandler(filters.Regex("^👤 Kullanıcı Adını Tanımla$"), handle_username_button))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, capture_username))

    telegram_app.run_polling()

if __name__ == "__main__":
    Thread(target=run_flask).start()
    run_bot()
