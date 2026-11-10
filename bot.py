import logging
import json
import sqlite3
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)

# --- CONFIGURATION ---
BOT_TOKEN = "8813599415:AAHppiim_ryfE0jl4m95HAdBS4N4j0kYzVY"
MANAGER_CHAT_ID = 867366564
WEB_APP_URL = "https://addisuazaje12-prog.github.io/guder-bingo/"
MINIMUM_BALANCE = 10.0
CARD_PRICE = 10.0
TELEBIRR_ACCOUNT = "0913706670"
CBE_ACCOUNT = "1000000000000"

AUDIO_DIR = "audio"

AWAITING_AMOUNT, SELECTING_PAYMENT, AWAITING_RECEIPT = range(3)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

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
        CREATE TABLE IF NOT EXISTS sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            winner_name TEXT,
            phone_number TEXT,
            cartela_no INTEGER,
            total_pot REAL,
            manager_share REAL,
            winner_share REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id)
    balance = db_user[4] if db_user else 0.0
    update_user_balance(user.id, 0.0, user.full_name, user.username or "N/A")

    keyboard = [
        [InlineKeyboardButton("🎮 Play Game (ካርቴላ ይምረጡ)", web_app=WebAppInfo(url=WEB_APP_URL))],
        [InlineKeyboardButton("💳 Deposit (ገንዘብ አስገባ)", callback_data="start_deposit"), InlineKeyboardButton("💸 Withdraw (ገንዘብ አውጣ)", callback_data="withdraw_menu")],
        [InlineKeyboardButton("💳 Balance (ሂሳብ ሁኔታ)", callback_data="check_balance")],
        [InlineKeyboardButton("📞 Support (እርዳታ)", callback_data="support_menu"), InlineKeyboardButton("📖 Instructions (መመሪያ)", callback_data="instruction_menu")],
        [InlineKeyboardButton("🎁 Invite Friends (ጓደኛጋብዝ)", callback_data="invite_menu")],
        [InlineKeyboardButton("⚙️ Manager Controls (አስተዳዳሪ)", callback_data="manager_dashboard")]
    ]
    
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()
        await target.edit_message_text(
            f"👋 **Welcome to Guder Bingo**, {user.first_name}!\n\n"
            f"💵 **Wallet Balance:** `{balance:.2f} ETB`\n"
            f"⚠️ Minimum balance of **10.00 ETB** required to play.\n\n"
            f"👇 *የሚፈልጉትን አማራጭ ይጫኑ (Select an option below):*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        await target.reply_text(
            f"👋 **Welcome to Guder Bingo**, {user.first_name}!\n\n"
            f"💵 **Wallet Balance:** `{balance:.2f} ETB`\n"
            f"⚠️ Minimum balance of **10.00 ETB** required to play.\n\n"
            f"👇 *የሚፈልጉትን አማራጭ ይጫኑ (Select an option below):*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db_user = get_user(query.from_user.id)
    balance = db_user[4] if db_user else 0.0
    await query.message.reply_text(f"✅ <b>የእርስዎ ሂሳብ (Balance):</b> {balance:.2f} ETB", parse_mode="HTML")

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
            await update.message.reply_text("❌ Minimum deposit amount is **10 ETB**.", parse_mode="Markdown")
            return AWAITING_AMOUNT
            
        context.user_data["deposit_amount"] = amount
        
        keyboard = [
            [InlineKeyboardButton("📱 Telebirr (ቴሌብር)", callback_data="pay_telebirr")],
            [InlineKeyboardButton("🏦 CBE Birr (ንግድ ባንክ)", callback_data="pay_cbe")]
        ]
        await update.message.reply_text(
            f"✅ Amount: **{amount:.2f} ETB**\n\n"
            f"እባክዎ የሚከፍሉበትን የክፍያ አማራጭ ይምረጡ (Please select payment method):",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return SELECTING_PAYMENT
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number:")
        return AWAITING_AMOUNT

async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = context.user_data.get("deposit_amount", 10.0)
    is_telebirr = query.data == "pay_telebirr"
    method = "Telebirr" if is_telebirr else "CBE Birr"
    acc_num = TELEBIRR_ACCOUNT if is_telebirr else CBE_ACCOUNT
    
    msg = (
        f"💳 **{method} የክፍያ መመሪያ (Payment Details)**\n"
        f"የተጠየቀው መጠን: **{amount:.2f} ETB**\n\n"
        f"1. ከዚህ በታች ያለውን ቁጥር ጫን በማድረግ ኮፒ ያድርጉ:\n"
        f"`{acc_num}`\n\n"
        f"2. ከላይ ባለው አካውንት ገንዘብ ያስተላልፉ።\n"
        f"3. የከፈሉበትን ሙሉ የ SMS ደረሰኝ ኮፒ በማድረግ በዚህ ቻት ላይ ፔስት (Paste) በማድረግ ይላኩ።"
    )
    await query.edit_message_text(msg, parse_mode="Markdown")
    return AWAITING_RECEIPT

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    receipt = update.message.text
    amount = context.user_data.get("deposit_amount", 10.0)
    update_user_balance(user.id, amount, user.full_name, user.username or "N/A")
    new_bal = get_user(user.id)[4]

    await update.message.reply_text(f"🎉 **Deposit Successful!**\nCredited: `{amount:.2f} ETB`\nNew Balance: `{new_bal:.2f} ETB`", parse_mode="Markdown")
    
    if MANAGER_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=MANAGER_CHAT_ID, text=f"📥 **DEPOSIT AUDIT**\nPlayer: {user.full_name}\nID: `{user.id}`\nAmount: `{amount}`\nReceipt:\n`{receipt}`", parse_mode="Markdown")
        except Exception:
            pass
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Action cancelled.")
    return ConversationHandler.END

async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📞 <b>ድጋፍ (Support)</b>\nGuder bingo support (@Guderbingosupport)"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")

async def instruction_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📖 <b>Guder Bingo - መመሪያ (Instructions)</b>\n\n🎲 ከ1-800 ካርቴላዎች ውስጥ ይምረጡ። ቢያንስ 10 ብር ሚዛን ያስፈልጋል።"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")

async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🎁 Invite friends using your referral link:\nhttps://t.me/guderbingo_bot?start=ref_"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)

async def manager_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id != MANAGER_CHAT_ID:
        await query.message.reply_text("⛔ Unauthorized access.")
        return

    conn = sqlite3.connect("bingo.db")
    cursor = conn.cursor()
    cursor.execute("SELECT session_id, winner_name, phone_number, cartela_no, winner_share, manager_share, timestamp FROM sessions ORDER BY session_id DESC LIMIT 5")
    rows = cursor.fetchall()
    conn.close()

    text = "⚙️ <b>MANAGER CONTROL DASHBOARD</b>\n\n"
    if not rows:
        text += "No recent game sessions recorded yet."
    else:
        for r in rows:
            text += f"🔹 <b>Session #{r[0]}</b>\n👤 Winner: {r[1]} (Ph: {r[2]})\n🎯 Cartela: #{r[3]}\n💰 Winner Share (50%): {r[4]} ETB\n🏢 Manager Share (50%): {r[5]} ETB\n⏰ Time: {r[6]}\n\n"

    await query.message.reply_text(text, parse_mode="HTML")

async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id)
    balance = db_user[4] if db_user else 0.0

    try:
        data = json.loads(update.message.web_app_data.data)
    except Exception:
        return

    action = data.get("action")
    if balance < MINIMUM_BALANCE:
        await update.message.reply_text("⚠️ **Insufficient amount!** Deposit funds to play.")
        return

    if action == "join_round":
        cartela_no = data.get("cartela_no")
        update_user_balance(user.id, -CARD_PRICE, user.full_name, user.username or "N/A")
        await update.message.reply_text(f"✅ Registered for Cartela **#{cartela_no}**!", parse_mode="Markdown")
        
        audio_path = os.path.join(AUDIO_DIR, "joined.ogg")
        if os.path.exists(audio_path):
            try:
                await context.bot.send_voice(chat_id=user.id, voice=open(audio_path, "rb"))
            except Exception:
                pass

    elif action == "claim_bingo":
        total_pot = 500.0
        winner_share = total_pot * 0.5
        manager_share = total_pot * 0.5
        
        conn = sqlite3.connect("bingo.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sessions (winner_name, phone_number, cartela_no, total_pot, manager_share, winner_share) VALUES (?, ?, ?, ?, ?, ?)",
                       (user.full_name, db_user[3] if db_user else "N/A", 104, total_pot, manager_share, winner_share))
        conn.commit()
        conn.close()

        update_user_balance(user.id, winner_share)
        await update.message.reply_text(f"🎉 **BINGO CONFIRMED!**\nእንኳን ደስ አለዎት! {winner_share} ETB (50% Winner Pool) ወደ ሂሳብዎ ገብቷል። (Telebirr: {TELEBIRR_ACCOUNT})", parse_mode="Markdown")
        
        audio_path = os.path.join(AUDIO_DIR, "bingo_winner.ogg")
        if os.path.exists(audio_path):
            try:
                await context.bot.send_voice(chat_id=user.id, voice=open(audio_path, "rb"))
            except Exception:
                pass

        if MANAGER_CHAT_ID:
            await context.bot.send_message(chat_id=MANAGER_CHAT_ID, text=f"🚨 <b>WINNER RECORDED & AUDITED</b>\nPlayer: {user.full_name}\nPhone: {db_user[3]}\nWinner Share: {winner_share} ETB\nManager Revenue: {manager_share} ETB", parse_mode="HTML")

def main():
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = Application.builder().token(BOT_TOKEN).request(request).get_updates_request(request).build()

    deposit_handler = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_start), CallbackQueryHandler(deposit_start, pattern="^start_deposit$")],
        states={
            AWAITING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_deposit_amount)],
            SELECTING_PAYMENT: [CallbackQueryHandler(handle_payment_choice, pattern="^pay_")],
            AWAITING_RECEIPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receipt)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("deposit", deposit_start))
    app.add_handler(CommandHandler("support", support_cmd))
    app.add_handler(CommandHandler("instruction", instruction_cmd))
    app.add_handler(CommandHandler("get_invitation_link", invite_cmd))
    
    app.add_handler(CallbackQueryHandler(check_balance, pattern="^check_balance$"))
    app.add_handler(CallbackQueryHandler(support_cmd, pattern="^support_menu$"))
    app.add_handler(CallbackQueryHandler(instruction_cmd, pattern="^instruction_menu$"))
    app.add_handler(CallbackQueryHandler(invite_cmd, pattern="^invite_menu$"))
    app.add_handler(CallbackQueryHandler(manager_dashboard, pattern="^manager_dashboard$"))
    app.add_handler(CallbackQueryHandler(deposit_start, pattern="^start_deposit$"))
    
    app.add_handler(deposit_handler)
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))

    print("🤖 Guder Bingo Server Active...")
    app.run_polling()

if __name__ == "__main__":
    main()
