import asyncio
import html
import logging
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
MANAGER_CHAT_ID_RAW = os.environ.get("MANAGER_CHAT_ID", "").strip()
MANAGER_CHAT_ID = int(MANAGER_CHAT_ID_RAW) if MANAGER_CHAT_ID_RAW.lstrip("-").isdigit() else None
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://addisuazaje12-prog.github.io/guder-bingo/").strip()
DB_PATH = os.environ.get("GUDER_DB_PATH", os.environ.get("DATABASE_PATH", "guder_bingo.db")).strip()

TELEBIRR_ACCOUNT = os.environ.get("TELEBIRR_ACCOUNT", "0913706670").strip()
CBE_ACCOUNT = os.environ.get("CBE_ACCOUNT", "1000338713952").strip()
MIN_DEPOSIT_ETB = 10.0

AWAITING_AMOUNT, SELECTING_PAYMENT, AWAITING_RECEIPT = range(3)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=15000;")
    return conn


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server listening on port %s", port)


def init_db():
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT UNIQUE,
                phone_number TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_transactions (
                txn_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount REAL,
                type TEXT,
                status TEXT DEFAULT 'CONFIRMED',
                reference_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        ledger_cols = [r["name"] for r in conn.execute("PRAGMA table_info(ledger_transactions)").fetchall()]
        if "status" not in ledger_cols:
            conn.execute("ALTER TABLE ledger_transactions ADD COLUMN status TEXT DEFAULT 'CONFIRMED'")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS deposit_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                gateway TEXT,
                receipt_text TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
    finally:
        conn.close()


init_db()


def get_user_balance(user_id: int) -> float:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0.0) AS bal
            FROM ledger_transactions
            WHERE user_id = ? AND status = 'CONFIRMED'
            """,
            (user_id,),
        ).fetchone()
        return float(row["bal"]) if row else 0.0
    finally:
        conn.close()


def get_or_create_user(telegram_id, username="", full_name="") -> int:
    tg_str = str(telegram_id)
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute("SELECT user_id FROM users WHERE telegram_id = ?", (tg_str,)).fetchone()
        if row:
            user_id = int(row["user_id"])
        else:
            cur = conn.execute(
                "INSERT INTO users (telegram_id, phone_number) VALUES (?, ?)",
                (tg_str, None),
            )
            user_id = int(cur.lastrowid)
        conn.execute("COMMIT;")
        return user_id
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def build_main_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🎮 Play Game", web_app=WebAppInfo(url=WEB_APP_URL))],
        [
            InlineKeyboardButton("💰 Deposit", callback_data="start_deposit"),
            InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_menu"),
        ],
        [InlineKeyboardButton("💳 Balance", callback_data="check_balance")],
        [
            InlineKeyboardButton("📞 Support", callback_data="support_menu"),
            InlineKeyboardButton("📖 Instructions", callback_data="instruction_menu"),
        ],
        [InlineKeyboardButton("🎁 Invite Friends", callback_data="invite_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = get_or_create_user(user.id, user.username or "", user.full_name or "")
    balance = get_user_balance(user_id)

    safe_name = html.escape(user.first_name or "Player")
    message_text = (
        f"👋 <b>Welcome to Guder Bingo</b>, {safe_name}!\n\n"
        f"💵 <b>Wallet Balance:</b> <code>{balance:.2f} ETB</code>\n\n"
        f"👇 <i>የሚፈልጉትን አማራጭ ይጫኑ:</i>"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            message_text,
            reply_markup=build_main_keyboard(),
            parse_mode="HTML",
        )
    elif update.message:
        await update.message.reply_text(
            message_text,
            reply_markup=build_main_keyboard(),
            parse_mode="HTML",
        )


async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = get_or_create_user(query.from_user.id)
    balance = get_user_balance(user_id)
    await query.message.reply_text(
        f"✅ <b>የእርስዎ ሂሳብ (Balance):</b> <code>{balance:.2f} ETB</code>",
        parse_mode="HTML",
    )


async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = f"💰 የሚፈልጉትን መጠን ከ{MIN_DEPOSIT_ETB:.0f} ብር ጀምሮ ያስገቡ::"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt)
    elif update.message:
        await update.message.reply_text(prompt)
    return AWAITING_AMOUNT


async def handle_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    try:
        amount = float(text)
        if amount < MIN_DEPOSIT_ETB:
            await update.message.reply_text(
                f"❌ Minimum deposit amount is <b>{MIN_DEPOSIT_ETB:.0f} ETB</b>.",
                parse_mode="HTML",
            )
            return AWAITING_AMOUNT

        context.user_data["deposit_amount"] = amount

        keyboard = [
            [
                InlineKeyboardButton("📱 Telebirr", callback_data="pay_telebirr"),
                InlineKeyboardButton("🏦 CBE Birr", callback_data="pay_cbe"),
            ]
        ]
        await update.message.reply_text(
            f"ተጠቃሚ\n"
            f"መጠን: <b>{amount:.2f} ብር</b>\n"
            f"ሂደ ይምረጡ:-",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return SELECTING_PAYMENT
    except ValueError:
        await update.message.reply_text("❌ እባክዎት ትክክለኛ ቁጥር ያስገቡ:")
        return AWAITING_AMOUNT


async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = float(context.user_data.get("deposit_amount", MIN_DEPOSIT_ETB))
    is_telebirr = query.data == "pay_telebirr"
    method = "Tele-Birr" if is_telebirr else "CBE Birr"
    acc_num = TELEBIRR_ACCOUNT if is_telebirr else CBE_ACCOUNT

    context.user_data["gateway"] = method

    msg = (
        f"📱 <b>{html.escape(method)}</b>\n\n"
        f"<code>{html.escape(acc_num)}</code>\n\n"
        f"የክፍያ መመሪያ:-\n\n"
        f"1. ከላይ ባለው የ {html.escape(method)} አካውንት {amount:.2f} ብር ያስገቡ\n"
        f"2. የከፈሉበትን የግብይት መረጃ የያዘ አጭር መልእክት (SMS) ከ {html.escape(method)} ይደርስዎታል።\n"
        f"3. ያገኙትን SMS ሙሉ ኮፒ በማድረግ ከዚህ በታች ባለው አጭር መልእክት ቦታ ውስጥ ፖስት በማድረግ ይላኩት።\n\n"
        f"📞 ለ ድጋፍ ወይም ለገጠሙ ችግር\n"
        f"1. Guder bingo support (@Guderbingosupport)"
    )
    await query.message.reply_text(msg, parse_mode="HTML")
    return AWAITING_RECEIPT


async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    receipt = (update.message.text or "").strip()
    if not receipt:
        await update.message.reply_text("❌ እባክዎት የክፍያ SMS ወይም ማረጋገጫ ጽሁፍ ያስገቡ:")
        return AWAITING_RECEIPT

    amount = float(context.user_data.get("deposit_amount", MIN_DEPOSIT_ETB))
    gateway = str(context.user_data.get("gateway", "Tele-Birr"))
    user_id = get_or_create_user(user.id, user.username or "", user.full_name or "")

    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        cur = conn.execute(
            """
            INSERT INTO deposit_requests (user_id, amount, gateway, receipt_text, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (user_id, amount, gateway, receipt),
        )
        req_id = int(cur.lastrowid)
        conn.execute("COMMIT;")
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()

    await update.message.reply_text(
        f"✅ የክፍያ ማረጋገጫዎ ደርሷል!\n\n"
        f"💵 መጠን: <b>{amount:.2f} ETB</b>\n"
        f"⏳ አስተዳዳሪው (Manager) እስኪያረጋግጥ ድረስ በትዕግስት ይጠብቁ። ከተረጋገጠ በኋላ ቦርሳዎ ይሞላል።",
        parse_mode="HTML",
    )

    if MANAGER_CHAT_ID:
        try:
            manager_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{req_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{req_id}"),
                ]
            ])
            await context.bot.send_message(
                chat_id=MANAGER_CHAT_ID,
                text=(
                    f"📥 <b>NEW DEPOSIT REQUEST #{req_id}</b>\n"
                    f"Player: {html.escape(user.full_name or 'Unknown')}\n"
                    f"Telegram ID: <code>{user.id}</code>\n"
                    f"Gateway: {html.escape(gateway)}\n"
                    f"Amount: <code>{amount:.2f}</code> ETB\n\n"
                    f"Receipt:\n<code>{html.escape(receipt)}</code>"
                ),
                reply_markup=manager_keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Failed to notify manager: %s", e)

    context.user_data.pop("deposit_amount", None)
    context.user_data.pop("gateway", None)
    return ConversationHandler.END


async def manager_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if MANAGER_CHAT_ID and query.from_user.id != MANAGER_CHAT_ID:
        await query.message.reply_text("❌ Only the configured manager can verify deposit requests.")
        return

    parts = (query.data or "").split("_", 1)
    if len(parts) != 2 or parts[0] not in ("approve", "reject") or not parts[1].isdigit():
        return

    action = parts[0]
    req_id = int(parts[1])

    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        req_row = conn.execute(
            "SELECT id, user_id, amount, status FROM deposit_requests WHERE id = ?",
            (req_id,),
        ).fetchone()

        if not req_row:
            conn.execute("ROLLBACK;")
            await query.edit_message_text(f"⚠️ Deposit Request #{req_id} not found.")
            return

        if req_row["status"] != "pending":
            conn.execute("ROLLBACK;")
            await query.edit_message_text(
                f"ℹ️ Deposit Request #{req_id} was already marked <b>{html.escape(req_row['status'].upper())}</b>.",
                parse_mode="HTML",
            )
            return

        target_user_id = int(req_row["user_id"])
        amount = float(req_row["amount"])

        if action == "approve":
            conn.execute("UPDATE deposit_requests SET status = 'approved' WHERE id = ?", (req_id,))
            txn_id = f"deposit_req_{req_id}_{int(time.time())}"
            conn.execute(
                """
                INSERT INTO ledger_transactions (txn_id, user_id, amount, type, status, reference_id)
                VALUES (?, ?, ?, 'DEPOSIT', 'CONFIRMED', ?)
                """,
                (txn_id, target_user_id, amount, f"deposit_req_{req_id}"),
            )
            u_row = conn.execute("SELECT telegram_id FROM users WHERE user_id = ?", (target_user_id,)).fetchone()
            conn.execute("COMMIT;")

            telegram_id = int(u_row["telegram_id"]) if u_row and u_row["telegram_id"] and u_row["telegram_id"].isdigit() else None
            new_bal = get_user_balance(target_user_id)

            await query.edit_message_text(
                f"✅ Deposit Request #{req_id} <b>APPROVED</b>. User credited with {amount:.2f} ETB.",
                parse_mode="HTML",
            )
            if telegram_id:
                try:
                    await context.bot.send_message(
                        chat_id=telegram_id,
                        text=(
                            f"🎉 <b>የክፍያ ጥያቄዎ ጸድቋል!</b>\n\n"
                            f"💵 {amount:.2f} ETB ወደ አካውንትዎ ገቢ ተደርጓል።\n"
                            f"💰 አጠቃላይ ሚዛን: <code>{new_bal:.2f} ETB</code>"
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("Failed to notify user %s: %s", telegram_id, e)
        else:
            conn.execute("UPDATE deposit_requests SET status = 'rejected' WHERE id = ?", (req_id,))
            conn.execute("COMMIT;")
            await query.edit_message_text(
                f"❌ Deposit Request #{req_id} <b>REJECTED</b>.",
                parse_mode="HTML",
            )
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("deposit_amount", None)
    context.user_data.pop("gateway", None)
    if update.message:
        await update.message.reply_text("❌ Action cancelled.")
    return ConversationHandler.END


async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💸 <b>Withdrawal (ገንዘብ ማውጣት)</b>\n\n"
        "የገንዘብ ማውጣት አገልግሎት በቅርብ የሚጀመር ይሆናል!\n"
        "ለማንኛውም ጥያቄ ድጋፍ ሰጪዎቻችንን ያነጋግሩ: @Guderbingosupport"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML")


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📞 <b>ድጋፍ (Support)</b>\nGuder bingo support (@Guderbingosupport)"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML")


async def instruction_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📖 <b>Guder Bingo - መመሪያ</b>\n\n🎲 ከ1-800 ካርቴላዎች ውስጥ ይምረጡ። ቢያንስ 10 ብር ሚዛን ያስፈልጋል።"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML")


async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id if update.effective_user else ""
    text = f"🎁 Invite friends using your referral link:\nhttps://t.me/guderbingo_bot?start=ref_{tg_id}"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    elif update.message:
        await update.message.reply_text(text)


async def configure_webapp_menu(application):
    if WEB_APP_URL.startswith("https://"):
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Guder Bingo",
                web_app=WebAppInfo(url=WEB_APP_URL),
            )
        )
        logger.info("Guder Bingo Mini App Menu Button configured.")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required.")

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
            CallbackQueryHandler(deposit_start, pattern="^start_deposit$"),
        ],
        states={
            AWAITING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_deposit_amount)],
            SELECTING_PAYMENT: [CallbackQueryHandler(handle_payment_choice, pattern="^pay_")],
            AWAITING_RECEIPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receipt)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
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

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    start_health_server()
    logger.info("Guder Bingo Bot Active with Ledger Integration...")
    app.run_polling()


if __name__ == "__main__":
    main()
