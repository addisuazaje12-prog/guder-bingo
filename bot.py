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
        [InlineKeyboardButton("🎮 Play Game", web_app=WebAppInfo(url=WEB_APP_URL))],
        [InlineKeyboardButton("💰 Deposit", callback_data="start_deposit"), InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_menu")],
        [InlineKeyboardButton("💳 Balance", callback_data="check_balance")],
        [InlineKeyboardButton("📞 Support", callback_data="support_menu"), InlineKeyboardButton("📖 Instructions", callback_data="instruction_menu")],
        [InlineKeyboardButton("🎁 Invite Friends", callback_data="invite_menu")]
    ]
    
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()
        await target.edit_message_text(
            f"👋 **Welcome to Guder Bingo**, {user.first_name}!\n\n"
            f"💵 **Wallet Balance:** `{balance:.2f} ETB`\n\n"
            f"👇 *የሚፈልጉትን አማራጭ ይጫኑ:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        await target.reply_text(
            f"👋 **Welcome to Guder Bingo**, {user.first_name}!\n\n"
            f"💵 **Wallet Balance:** `{balance:.2f} ETB`\n\n"
            f"👇 *የሚፈልጉትን አማራጭ ይጫኑ:*",
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
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("💰 የሚፈልጉትን መጠን ከ10 ብር ጀምሮ ያስገቡ::")
    else:
        await update.message.reply_text("💰 የሚፈልጉትን መጠን ከ10 ብር ጀምሮ ያስገቡ::")
    return AWAITING_AMOUNT

async def handle_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        amount = float(text)
        if amount < 10.0:
            await update.message.reply_text("❌ Minimum deposit amount is **10 ETB**.", parse_mode="Markdown")
            return AWAITING_AMOUNT
            
        context.user_data["deposit_amount"] = amount
        
        keyboard = [
            [InlineKeyboardButton("📱 Telebirr", callback_data="pay_telebirr"), InlineKeyboardButton("🏦 CBE Birr", callback_data="pay_cbe")]
        ]
        await update.message.reply_text(
            f"ተጠቃሚ\n"
            f"መጠን: **{amount:.2f} ብር**\n"
            f"ሂደ ይምረጡ:-",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return SELECTING_PAYMENT
    except ValueError:
        await update.message.reply_text("❌ እባክዎ ትክክለኛ ቁጥር ያስገቡ:")
        return AWAITING_AMOUNT

async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = context.user_data.get("deposit_amount", 10.0)
    is_telebirr = query.data == "pay_telebirr"
    method = "Tele-Birr" if is_telebirr else "CBE Birr"
    acc_num = TELEBIRR_ACCOUNT if is_telebirr else CBE_ACCOUNT
    
    msg = (
        f"📱 **{method}**\n\n"
        f"`{acc_num}`\n\n"
        f"የቴሌብር መመሪያ:-\n\n"
        f"1. ከላይ ባለው የ {method} አካውንት {amount:.2f} ብር ያስገቡ\n"
        f"2. የከፈሉበትን የግብይት መረጃ የያዘ አጭር መልእክት (SMS) ከ {method} ይደርስዎታል።\n"
        f"3. ያገኙትን SMS ሙሉ ኮፒ በማድረግ ከዚህ በታች ባለው አጭር መልእክት ቦታ ውስጥ ፖስት በማድረግ ይላኩት።\n\n"
        f"📞 ለ ድጋፍ ወይም ለገጠሙ ችግር\n"
        f"1. Guder bingo support (@Guderbingosupport)"
    )
    await query.message.reply_text(msg, parse_mode="Markdown")
    return AWAITING_RECEIPT

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    receipt = update.message.text
    amount = context.user_data.get("deposit_amount", 10.0)
    update_user_balance(user.id, amount, user.full_name, user.username or "N/A")
    new_bal = get_user(user.id)[4]

    await update.message.reply_text(
        f"✅ {amount:.2f} በተሳካ ሁኔታ ገቢ ተደርጓል::\n\n"
        f"ጠቅላላ : {new_bal:.2f}\n\n"
        f"📞 ለ ድጋፍ ወይም ለገጠሙ ችግር\n"
        f"1. Guder bingo support (@Guderbingosupport)",
        parse_mode="Markdown"
    )
    
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
    text = "📖 <b>Guder Bingo - መመሪያ</b>\n\n🎲 ከ1-800 ካርቴላዎች ውስጥ ይምረጡ። ቢያንስ 10 ብር ሚዛን ያስፈልጋል።"
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

def main():
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = Application.builder().token(BOT_TOKEN).request(request).get_updates_request(request).build()

    deposit_handler = ConversationHandler(
        entry_points=[
            CommandHandler("deposit", deposit_start),
            CallbackQueryHandler(deposit_start, pattern="^start_deposit$")
        ],
        states={
            AWAITING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_deposit_amount)],
            SELECTING_PAYMENT: [CallbackQueryHandler(handle_payment_choice, pattern="^pay_")],
            AWAITING_RECEIPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receipt)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )

    app.add_handler(deposit_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("support", support_cmd))
    app.add_handler(CommandHandler("instruction", instruction_cmd))
    app.add_handler(CommandHandler("get_invitation_link", invite_cmd))
    
    app.add_handler(CallbackQueryHandler(check_balance, pattern="^check_balance$"))
    app.add_handler(CallbackQueryHandler(support_cmd, pattern="^support_menu$"))
    app.add_handler(CallbackQueryHandler(instruction_cmd, pattern="^instruction_menu$"))
    app.add_handler(CallbackQueryHandler(invite_cmd, pattern="^invite_menu$"))

    print("🤖 Guder Bingo Server Active...")
    app.run_polling()

if __name__ == "__main__":
    main()
