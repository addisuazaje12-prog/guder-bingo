import logging
import json
import sqlite3
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)

# --- CONFIGURATION ---
BOT_TOKEN = "8813599415:AAE2wMKTEIRPLhP2jbGDVthpe_DrPbLmhEo"  # Insert your active Bot Father Token
MANAGER_CHAT_ID = 867366564       # Insert your numeric User ID from @userinfobot
WEB_APP_URL = "https://addisuazaje12-prog.github.io/guder-bingo/"
MINIMUM_BALANCE = 10.0            # Rule 8: 10 ETB minimum required
CARD_PRICE = 10.0                 # Entry fee per cartela/card

# --- CONVERSATION STATES ---
AWAITING_AMOUNT, SELECTING_PAYMENT, AWAITING_RECEIPT = range(3)

# --- LOGGING SETUP ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect("bingo.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            phone_number TEXT DEFAULT 'N/A',
            balance REAL DEFAULT 0.0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            method TEXT,
            receipt TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS round_history (
            round_id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_players INTEGER,
            total_pool REAL,
            winner_pool REAL,
            manager_revenue REAL,
            winner_ids TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- DATABASE HELPERS ---
def get_user(user_id):
    conn = sqlite3.connect("bingo.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, full_name, phone_number, balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def update_user_balance(user_id, amount, full_name="", username="", phone="N/A"):
    conn = sqlite3.connect("bingo.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, username, full_name, phone_number, balance)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            balance = balance + excluded.balance,
            full_name = excluded.full_name,
            username = excluded.username
    """, (user_id, username, full_name, phone, amount))
    conn.commit()
    conn.close()

# --- GAME ENGINE STATE ---
class GameEngine:
    def __init__(self):
        self.state = "REGISTRATION"  # REGISTRATION, PREPARING, CALLING, DISPLAY
        self.round_id = 1
        self.participants = {}      # {user_id: {"cartela_no": int, "user": User}}
        self.drawn_numbers = []
        self.winners = []
        self.timer_seconds = 30

    def reset_round(self):
        self.round_id += 1
        self.state = "REGISTRATION"
        self.participants = {}
        self.drawn_numbers = []
        self.winners = []
        self.timer_seconds = 30

game = GameEngine()

# --- CORE TELEGRAM COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id)
    balance = db_user[4] if db_user else 0.0

    # Ensure user is recorded
    update_user_balance(user.id, 0.0, user.full_name, user.username or "N/A")

    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo Mini App", web_app={"url": WEB_APP_URL})],
        [InlineKeyboardButton("💳 Deposit", callback_data="start_deposit"), InlineKeyboardButton("💰 Balance", callback_data="check_balance")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"👋 **Welcome to Guder Bingo**, {user.first_name}!\n\n"
        f"💵 **Wallet Balance:** `{balance:.2f} ETB`\n"
        f"⚠️ **Note:** A minimum balance of **10.00 ETB** is required to join rounds.\n\n"
        f" Tap **Play Bingo Mini App** to join the live game board!",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db_user = get_user(query.from_user.id)
    balance = db_user[4] if db_user else 0.0
    await query.message.reply_text(f"💰 **Current Wallet Balance:** `{balance:.2f} ETB`", parse_mode="Markdown")

# --- TELEBIRR DEPOSIT CONVERSATION FLOW ---
async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()

    await target.reply_text("💳 **Deposit Request**\nEnter the amount you wish to deposit (Minimum 10 ETB):")
    return AWAITING_AMOUNT

async def handle_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount < 10.0:
            await update.message.reply_text("❌ Minimum deposit amount is **10 ETB**. Please re-enter amount:", parse_mode="Markdown")
            return AWAITING_AMOUNT
        
        context.user_data["deposit_amount"] = amount
        keyboard = [
            [InlineKeyboardButton("📱 Telebirr", callback_data="pay_telebirr")],
            [InlineKeyboardButton("🏦 CBE Birr", callback_data="pay_cbe")],
            [InlineKeyboardButton("❓ Help / Support", callback_data="pay_help")]
        ]
        await update.message.reply_text(
            f"✅ Requested Amount: **{amount:.2f} ETB**\nSelect payment method:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return SELECTING_PAYMENT
    except ValueError:
        await update.message.reply_text("❌ Invalid input. Please type a number (e.g. 50 or 100):")
        return AWAITING_AMOUNT

async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = context.user_data.get("deposit_amount", 10.0)

    if query.data == "pay_telebirr":
        msg = (
            f"📱 **Telebirr Payment Details**\n\n"
            f"💵 Amount: **{amount:.2f} ETB**\n"
            f"👤 Merchant Account: `0913706670`\n\n"
            f"**Instructions:**\n"
            f"1. Open Telebirr app.\n"
            f"2. Send **{amount:.2f} ETB** to `0913706670`.\n"
            f"3. Copy and **paste the full SMS transaction receipt** into this chat."
        )
        await query.edit_message_text(msg, parse_mode="Markdown")
        return AWAITING_RECEIPT
    elif query.data == "pay_cbe":
        await query.edit_message_text("🏦 **CBE Birr Details**\n\nTransfer to `0913706670` and paste the transaction confirmation SMS below:", parse_mode="Markdown")
        return AWAITING_RECEIPT
    else:
        await query.edit_message_text("❓ Please contact manager support for manual verification.")
        return ConversationHandler.END

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    receipt = update.message.text
    amount = context.user_data.get("deposit_amount", 10.0)

    update_user_balance(user.id, amount, user.full_name, user.username or "N/A")
    new_bal = get_user(user.id)[4]

    await update.message.reply_text(
        f"🎉 **Deposit Successful!**\n\n"
        f"✅ Credited: `{amount:.2f} ETB`\n"
        f"💵 New Balance: `{new_bal:.2f} ETB`\n"
        f"You can now join live rounds!",
        parse_mode="Markdown"
    )

    # Manager Audit Log
    if MANAGER_CHAT_ID and MANAGER_CHAT_ID != 123456789:
        try:
            await context.bot.send_message(
                chat_id=MANAGER_CHAT_ID,
                text=f"📥 **DEPOSIT AUDIT LOG**\n👤 Player: {user.full_name} (@{user.username})\n🆔 ID: `{user.id}`\n💰 Amount: `{amount:.2f} ETB`\n🧾 Receipt:\n`{receipt}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Manager log error: {e}")

    return ConversationHandler.END

# --- WEB APP INTERACTION (JOIN & WIN CLAIMS) ---
async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id)
    balance = db_user[4] if db_user else 0.0

    data = json.loads(update.message.web_app_data.data)
    action = data.get("action")

    # Check Rule 8: Minimum Balance Requirement
    if balance < MINIMUM_BALANCE:
        await update.message.reply_text("⚠️ **Insufficient Balance!**\nYou must maintain a minimum balance of **10.00 ETB** to join or claim wins.", parse_mode="Markdown")
        return

    if action == "join_round":
        cartela_no = data.get("cartela_no")
        if game.state != "REGISTRATION":
            await update.message.reply_text("❌ Registration for the current round is closed!")
            return

        if len(game.participants) >= 800:
            await update.message.reply_text("❌ Round is full (Maximum 800 players limit reached)!")
            return

        # Deduct entry fee
        update_user_balance(user.id, -CARD_PRICE, user.full_name, user.username or "N/A")
        game.participants[user.id] = {"cartela_no": cartela_no, "user": user}

        await update.message.reply_text(f"✅ **Registered!** You were assigned Cartela **#{cartela_no}** for Round #{game.round_id}.", parse_mode="Markdown")

    elif action == "claim_bingo":
        if user.id in game.participants and user.id not in game.winners:
            game.winners.append(user.id)
            await update.message.reply_text("🎉 **BINGO CLAIM SUBMITTED!** System verifying winning pattern...")

# --- MAIN ENGINE LIFECYCLE ---
def main():
    # Increase HTTP timeout to 30 seconds to prevent network dropouts / timeout crashes
    custom_request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    
    app = Application.builder().token(BOT_TOKEN).request(custom_request).build()

    deposit_handler = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_start), CallbackQueryHandler(deposit_start, pattern="^start_deposit$")],
        states={
            AWAITING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_deposit_amount)],
            SELECTING_PAYMENT: [CallbackQueryHandler(handle_payment_choice, pattern="^pay_")],
            AWAITING_RECEIPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receipt)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_balance, pattern="^check_balance$"))
    app.add_handler(deposit_handler)
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))

    print("🤖 Guder Bingo Server Active...")
    app.run_polling()

if __name__ == "__main__":
    main()