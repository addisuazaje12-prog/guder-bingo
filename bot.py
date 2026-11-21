import logging
import os
import sqlite3
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo, MenuButtonWebApp
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)

# --- CONFIGURATION ---
BOT_TOKEN = "8813599415:AAHWQPz4kSu2MNaK1eFTgfhi59GZwzR7Xg0"
MANAGER_CHAT_ID = 867366564
WEB_APP_URL = "https://addisuazaje12-prog.github.io/guder-bingo/"
DB_PATH = os.environ.get("GUDER_DB_PATH", "guder_bingo.db")

TELEBIRR_ACCOUNT = "0913706670"
CBE_ACCOUNT = "1000338713952"

AWAITING_AMOUNT, SELECTING_PAYMENT, AWAITING_RECEIPT = range(3)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE,
            phone_number TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ledger_transactions (
            txn_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            type TEXT,
            reference_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deposit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            gateway TEXT,
            receipt_text TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_user_by_telegram(telegram_id):
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, telegram_id, phone_number FROM users WHERE telegram_id = ?", (str(telegram_id),))
    row = cursor.fetchone()
    conn.close()
    return row

def get_user_balance(user_id):
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(amount) FROM ledger_transactions WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result and result[0] is not None else 0.0

def get_or_create_user(telegram_id, username="", full_name=""):
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE telegram_id = ?", (str(telegram_id),))
    row = cursor.fetchone()
    if row:
        user_id = row[0]
    else:
        cursor.execute("INSERT INTO users (telegram_id, phone_number) VALUES (?, ?)", (str(telegram_id), '251913706670'))
        user_id = cursor.lastrowid
        conn.commit()
    conn.close()
    return user_id

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = get_or_create_user(user.id, user.username, user.full_name)
    balance = get_user_balance(user_id)

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
    user_id = get_or_create_user(query.from_user.id)
    balance = get_user_balance(user_id)
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
        await update.message.reply_text("❌ እባክዎት ትክክለኛ ቁጥር ያስገቡ:")
        return AWAITING_AMOUNT

async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = context.user_data.get("deposit_amount", 10.0)
    is_telebirr = query.data == "pay_telebirr"
    method = "Tele-Birr" if is_telebirr else "CBE Birr"
    acc_num = TELEBIRR_ACCOUNT if is_telebirr else CBE_ACCOUNT
    
    context.user_data["gateway"] = method
    
    msg = (
        f"📱 **{method}**\n\n"
        f"`{acc_num}`\n\n"
        f"የክፍያ መመሪያ:-\n\n"
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
    gateway = context.user_data.get("gateway", "Tele-Birr")
    user_id = get_or_create_user(user.id, user.username, user.full_name)

    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO deposit_requests (user_id, amount, gateway, receipt_text, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (user_id, amount, gateway, receipt))
    req_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ የክፍያ ማረጋገጫዎ ደርሷል!\n\n"
        f"💵 መጠን: {amount:.2f} ETB\n"
        f"⏳ አስተዳዳሪው (Manager) እስኪያረጋግጥ ድረስ በትዕግስት ይጠብቁ። ከተረጋገጠ በኋላ ቦርሳዎ ይሞላል።",
        parse_mode="Markdown"
    )
    
    if MANAGER_CHAT_ID:
        try:
            manager_keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{req_id}_{user_id}_{amount}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{req_id}")
                ]
            ]
            await context.bot.send_message(
                chat_id=MANAGER_CHAT_ID,
                text=f"📥 **NEW DEPOSIT REQUEST #{req_id}**\n"
                     f"Player: {user.full_name}\n"
                     f"Telegram ID: `{user.id}`\n"
                     f"Gateway: {gateway}\n"
                     f"Amount: `{amount}` ETB\n\n"
                     f"Receipt:\n`{receipt}`",
                reply_markup=InlineKeyboardMarkup(manager_keyboard),
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Failed to notify manager: {e}")
            
    return ConversationHandler.END

async def manager_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    action = data[0]
    req_id = data[1]

    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    cursor = conn.cursor()

    if action == "approve":
        target_user_id = int(data[2])
        amount = float(data[3])

        cursor.execute("UPDATE deposit_requests SET status = 'approved' WHERE id = ?", (req_id,))
        
        txn_id = f"deposit_req_{req_id}_{int(time.time())}"
        cursor.execute('''
            INSERT INTO ledger_transactions (txn_id, user_id, amount, type, reference_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (txn_id, target_user_id, amount, 'DEPOSIT', f"deposit_req_{req_id}"))
        
        conn.commit()
        
        cursor.execute("SELECT telegram_id FROM users WHERE user_id = ?", (target_user_id,))
        u_row = cursor.fetchone()
        telegram_id = int(u_row[0]) if u_row and u_row[0].isdigit() else None
        
        conn.close()

        new_bal = get_user_balance(target_user_id)

        await query.edit_message_text(f"✅ Deposit Request #{req_id} **APPROVED**. User credited with {amount} ETB.")
        if telegram_id:
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=f"🎉 **የክፍያ ጥያቄዎ ጸድቋል!**\n\n💵 {amount:.2f} ETB ወደ አካውንትዎ ገቢ ተደርጓል።\n💰 አጠቃላይ ሚዛን: {new_bal:.2f} ETB",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
    else:
        cursor.execute("UPDATE deposit_requests SET status = 'rejected' WHERE id = ?", (req_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text(f"❌ Deposit Request #{req_id} **REJECTED**.")

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Action cancelled.")
    return ConversationHandler.END

async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "💸 **Withdrawal (ገንዘብ ማውጣት)**\n\n"
        "የገንዘብ ማውጣት አገልግሎት በቅርብ የሚጀመር ይሆናል!\n"
        "ለማንኛውም ጥያቄ ድጋፍ ሰጪዎቻችንን ያነጋግሩ: @Guderbingosupport",
        parse_mode="Markdown"
    )

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

async def configure_webapp_menu(application):
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Guder Bingo",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )
    )
    logging.info("Guder Bingo Mini App Menu Button configured.")

def main():
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(request)
        .post_init(configure_webapp_menu)
        .build()
    )

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
    app.add_handler(CallbackQueryHandler(withdraw_cmd, pattern="^withdraw_menu$"))
    app.add_handler(CallbackQueryHandler(support_cmd, pattern="^support_menu$"))
    app.add_handler(CallbackQueryHandler(instruction_cmd, pattern="^instruction_menu$"))
    app.add_handler(CallbackQueryHandler(invite_cmd, pattern="^invite_menu$"))
    app.add_handler(CallbackQueryHandler(manager_decision, pattern="^(approve|reject)_"))

    print("🤖 Guder Bingo Bot Active with Ledger Integration...")
    app.run_polling()

if __name__ == "__main__":
    main()
