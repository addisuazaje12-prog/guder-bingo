import os
import logging
import asyncio
import requests

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# GUDER BINGO TELEGRAM BOT
# Compatible with the Guder Bingo Flask backend:
# POST /api/bot/deposit
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

APP_URL = os.environ.get(
    "APP_URL",
    "https://guder-bingo.ethiodeploy.app",
).strip().rstrip("/")

BACKEND_URL = os.environ.get("BACKEND_URL", APP_URL).strip().rstrip("/")
BOT_API_SECRET = os.environ.get("BOT_API_SECRET", "").strip()

TELEBIRR_NUMBER = os.environ.get("TELEBIRR_NUMBER", "0913706670").strip()
SUPPORT_NUMBER = os.environ.get("SUPPORT_NUMBER", "0913706670").strip()

REQUEST_TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("guder-bingo-bot")


# ============================================================
# BILINGUAL TELEGRAM MENU
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎮 Play a Game | ጨዋታ ይጀምሩ",
                web_app=WebAppInfo(url=APP_URL),
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Deposit | ገንዘብ ያስገቡ",
                callback_data="deposit",
            ),
            InlineKeyboardButton(
                "💳 Balance | ቀሪ ሂሳብ",
                callback_data="balance",
            ),
        ],
        [
            InlineKeyboardButton(
                "📤 Withdraw | ገንዘብ ያውጡ",
                callback_data="withdraw",
            ),
            InlineKeyboardButton(
                "🏆 History | የጨዋታ ታሪክ",
                callback_data="history",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Invite Friends | ጓደኞችን ይጋብዙ",
                callback_data="invite",
            )
        ],
        [
            InlineKeyboardButton(
                "📖 Instructions | መመሪያ",
                callback_data="instructions",
            ),
            InlineKeyboardButton(
                "☎ Support | ድጋፍ",
                callback_data="support",
            ),
        ],
    ])


# ============================================================
# START / MINI APP
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_for_receipt"] = False

    welcome = (
        "🎉 እንኳን ወደ ጉደር ቢንጎ በሰላም መጡ!\n\n"
        "Welcome to Guder Bingo!\n\n"
        "💰 ጨዋታ ለመጀመር በመጀመሪያ ገንዘብ ያስገቡ።\n"
        "Deposit before selecting a cartela.\n\n"
        "👀 በቀጥታ የሚካሄደውን ጨዋታ ያለ ቀሪ ሂሳብ መመልከት ይችላሉ።\n"
        "Everyone can watch the live game, but a sufficient wallet "
        "balance is required to select a cartela.\n\n"
        "Choose an option below. | ከታች ያለውን አማራጭ ይምረጡ።"
    )

    await update.effective_message.reply_text(
        welcome,
        reply_markup=main_keyboard(),
    )


async def open_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Open Guder Bingo | ጉደር ቢንጎን ይክፈቱ።",
        reply_markup=main_keyboard(),
    )


# ============================================================
# DEPOSIT INSTRUCTIONS
# ============================================================

async def deposit_instructions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data["waiting_for_receipt"] = True

    message = (
        "💰 GUDER BINGO DEPOSIT | የጉደር ቢንጎ ገንዘብ ማስገቢያ\n\n"
        f"TeleBirr number | የቴሌብር ቁጥር: {TELEBIRR_NUMBER}\n\n"
        "1. Transfer the amount using TeleBirr.\n"
        "   በቴሌብር ገንዘብ ያስተላልፉ።\n\n"
        "2. Open your transaction receipt and copy its complete text.\n"
        "   የክፍያ ደረሰኙን ከፍተው ሙሉ ጽሑፉን ይቅዱ።\n\n"
        "3. Return here and send the receipt text as a message.\n"
        "   ወደዚህ ተመልሰው የደረሰኙን ጽሑፍ ይላኩ።\n\n"
        "After the system accepts the receipt, provisional playing "
        "credit is added immediately. The manager may review the "
        "payment later.\n"
        "ስርዓቱ ደረሰኙን ከተቀበለ በኋላ ጊዜያዊ የጨዋታ ሂሳብ "
        "ወዲያውኑ ይጨመራል። አስተዳዳሪው ክፍያውን በኋላ ሊያረጋግጥ ይችላል።\n\n"
        "Send your complete receipt now. | አሁን ሙሉ ደረሰኙን ይላኩ።\n"
        "Send /cancel to stop. | ለማቋረጥ /cancel ይጻፉ።"
    )

    await update.effective_message.reply_text(message)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_for_receipt"] = False

    await update.effective_message.reply_text(
        "Receipt submission cancelled. | የደረሰኝ ማስገባት ተቋርጧል።\n\n"
        "Press Deposit to start again. | እንደገና ለመጀመር Deposit ይጫኑ።",
        reply_markup=main_keyboard(),
    )


# ============================================================
# RECEIPT SUBMISSION
# ============================================================

async def receive_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.effective_message

    if not message or not message.text or not user:
        return

    receipt = message.text.strip()

    if not context.user_data.get("waiting_for_receipt"):
        await message.reply_text(
            "To submit a payment receipt, press Deposit first.\n"
            "የክፍያ ደረሰኝ ለማስገባት መጀመሪያ Deposit ይጫኑ።",
            reply_markup=main_keyboard(),
        )
        return

    if len(receipt) < 10 or len(receipt) > 5000:
        await message.reply_text(
            "❌ The receipt text appears incomplete or too long.\n"
            "የደረሰኙ ጽሑፍ ያልተሟላ ወይም ከመጠን በላይ ረጅም ነው።\n\n"
            "Please send the complete receipt text.\n"
            "እባክዎ ሙሉ ደረሰኙን ይላኩ።"
        )
        return

    if not BOT_API_SECRET:
        logger.error("BOT_API_SECRET is missing")
        await message.reply_text(
            "❌ Deposit service configuration error. Please contact support.\n"
            "የክፍያ አገልግሎቱ ላይ የውቅር ችግር አለ። ድጋፍን ያነጋግሩ።"
        )
        return

    processing = await message.reply_text(
        "⏳ Receipt received. Processing...\n"
        "ደረሰኙ ደርሷል። በማስኬድ ላይ..."
    )

    payload = {
        "user_id": str(user.id),
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username or "",
        "receipt": receipt,
    }

    headers = {
        "X-Bot-Secret": BOT_API_SECRET,
        "Content-Type": "application/json",
    }

    try:
        response = await asyncio.to_thread(
            requests.post,
            f"{BACKEND_URL}/api/bot/deposit",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        try:
            data = response.json()
        except ValueError:
            logger.error(
                "Non-JSON backend response: HTTP %s: %s",
                response.status_code,
                response.text[:500],
            )
            await processing.edit_text(
                "❌ The deposit service returned an unexpected response. "
                "Please try later or contact support.\n"
                "የክፍያ አገልግሎቱ ያልተጠበቀ ምላሽ ሰጥቷል። ቆይተው ይሞክሩ "
                "ወይም ድጋፍን ያነጋግሩ።"
            )
            context.user_data["waiting_for_receipt"] = True
            return

        logger.info(
            "Deposit response user=%s HTTP=%s success=%s",
            user.id,
            response.status_code,
            data.get("success"),
        )

        if not (200 <= response.status_code < 300) or not data.get("success"):
            error = data.get(
                "error",
                data.get("message", "The receipt could not be processed."),
            )
            await processing.edit_text(
                "❌ DEPOSIT NOT PROCESSED | ክፍያው አልተሳካም\n\n"
                f"{error}\n\n"
                "Check the receipt and try again. | ደረሰኙን አረጋግጠው እንደገና ይሞክሩ።"
            )
            context.user_data["waiting_for_receipt"] = True
            return

        amount = data.get("amount")
        reference = data.get("reference", "")
        status = str(data.get("status", "")).upper()

        if status != "PROVISIONAL" or not reference or amount is None:
            logger.warning("Unexpected deposit response: %r", data)
            await processing.edit_text(
                "⚠️ The server did not return the expected deposit details. "
                "Please contact support before resubmitting this receipt.\n"
                "ስርዓቱ የሚጠበቀውን የክፍያ መረጃ አልመለሰም። ደረሰኙን እንደገና "
                "ከመላክዎ በፊት ድጋፍን ያነጋግሩ።"
            )
            context.user_data["waiting_for_receipt"] = False
            return

        context.user_data["waiting_for_receipt"] = False

        await processing.edit_text(
            "✅ RECEIPT SUBMITTED | ደረሰኙ ተቀብሏል!\n\n"
            f"Amount | መጠን: {amount} ETB\n"
            f"Transaction reference | የግብይት መለያ: {reference}\n\n"
            "Your provisional playing credit has been added. "
            "You can now open the Mini App and select an available cartela "
            "when selection is open.\n"
            "ጊዜያዊ የጨዋታ ሂሳብዎ ተጨምሯል። አሁን Mini App ከፍተው "
            "የካርቴላ ምርጫ ሲከፈት ካርቴላ መምረጥ ይችላሉ።\n\n"
            "Note: The manager may review the receipt later.\n"
            "ማሳሰቢያ፦ አስተዳዳሪው ደረሰኙን በኋላ ሊመረምር ይችላል።",
            reply_markup=main_keyboard(),
        )

    except requests.exceptions.Timeout:
        logger.exception("Deposit request timed out")
        await processing.edit_text(
            "⏳ The service timed out. Your receipt may already have been "
            "processed. Check your wallet in the Mini App before sending "
            "the same receipt again.\n"
            "አገልግሎቱ በጊዜ ውስጥ ምላሽ አልሰጠም። ደረሰኙ አስቀድሞ "
            "ተሰርቶ ሊሆን ይችላል። ደረሰኙን እንደገና ከመላክዎ በፊት "
            "በMini App የኪስ ቦርሳ ሂሳብዎን ይመልከቱ።"
        )
        context.user_data["waiting_for_receipt"] = False

    except requests.exceptions.RequestException:
        logger.exception("Deposit connection failed")
        await processing.edit_text(
            "❌ Could not connect to the deposit service. Please try later.\n"
            "ከክፍያ አገልግሎቱ ጋር መገናኘት አልተቻለም። ቆይተው ይሞክሩ።"
        )
        context.user_data["waiting_for_receipt"] = True

    except Exception:
        logger.exception("Unexpected receipt processing error")
        await processing.edit_text(
            "❌ An unexpected error occurred. Please contact support.\n"
            "ያልተጠበቀ ችግር ተፈጥሯል። እባክዎ ድጋፍን ያነጋግሩ።"
        )
        context.user_data["waiting_for_receipt"] = True


# ============================================================
# INLINE BUTTON HANDLER
# ============================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    user_id = update.effective_user.id if update.effective_user else 0

    if query.data == "deposit":
        await deposit_instructions(update, context)

    elif query.data == "balance":
        await query.message.reply_text(
            "💳 WALLET BALANCE | የኪስ ቦርሳ ሂሳብ\n\n"
            "Open the Guder Bingo Mini App and choose Wallet/Balance "
            "to see your current balance.\n"
            "የአሁኑን ሂሳብ ለማየት Guder Bingo Mini App ከፍተው "
            "Wallet/Balance የሚለውን ይምረጡ።",
            reply_markup=main_keyboard(),
        )

    elif query.data == "withdraw":
        await query.message.reply_text(
            "📤 WITHDRAW | ገንዘብ ማውጣት\n\n"
            "Please use the withdrawal instructions shown in the Mini App "
            "or contact support. Do not send your PIN or password to anyone.\n"
            "እባክዎ በMini App የሚታየውን የገንዘብ ማውጫ መመሪያ ይከተሉ "
            "ወይም ድጋፍን ያነጋግሩ። PIN ወይም የይለፍ ቃልዎን ለማንም አይስጡ።",
            reply_markup=main_keyboard(),
        )

    elif query.data == "history":
        await query.message.reply_text(
            "🏆 GAME HISTORY | የጨዋታ ታሪክ\n\n"
            "Open Guder Bingo Mini App and select History to view "
            "the recorded winners and game results.\n"
            "የተመዘገቡ አሸናፊዎችንና የጨዋታ ውጤቶችን ለማየት "
            "Mini App ከፍተው History ይምረጡ።",
            reply_markup=main_keyboard(),
        )

    elif query.data == "invite":
        link = f"https://t.me/guderbingo_bot?start=ref_{user_id}"
        await query.message.reply_text(
            "👥 INVITE FRIENDS | ጓደኞችን ይጋብዙ\n\n"
            "Share this link with your friends:\n"
            "ይህንን ሊንክ ለጓደኞችዎ ያጋሩ፦\n\n"
            f"{link}",
            reply_markup=main_keyboard(),
        )

    elif query.data == "instructions":
        await query.message.reply_text(
            "📖 HOW TO PLAY | እንዴት መጫወት ይቻላል?\n\n"
            "1. Press Deposit and transfer money using TeleBirr.\n"
            "   Deposit ይጫኑና በቴሌብር ገንዘብ ያስተላልፉ።\n"
            "2. Send the complete receipt text in this bot chat.\n"
            "   ሙሉ የክፍያ ደረሰኙን በዚህ ቦት ውይይት ይላኩ።\n"
            "3. Open the Mini App. Everyone can watch the live game.\n"
            "   Mini App ይክፈቱ። ሁሉም ሰው ቀጥታ ጨዋታውን መመልከት ይችላል።\n"
            "4. You need at least the required wallet balance to select a cartela.\n"
            "   ካርቴላ ለመምረጥ በኪስ ቦርሳዎ ውስጥ በቂ ሂሳብ ሊኖር ይገባል።\n"
            "5. Watch the called numbers and press BINGO only when you have "
            "a valid winning pattern.\n"
            "   የሚጠሩትን ቁጥሮች ይከታተሉ፤ ትክክለኛ የአሸናፊነት ቅደም ተከተል "
            "ሲኖርዎት BINGO ይጫኑ።",
            reply_markup=main_keyboard(),
        )

    elif query.data == "support":
        await query.message.reply_text(
            "☎ GUDER BINGO SUPPORT | የጉደር ቢንጎ ድጋፍ\n\n"
            f"Support number | የድጋፍ ቁጥር: {SUPPORT_NUMBER}\n\n"
            "Contact support if you have a receipt or game problem.\n"
            "የደረሰኝ ወይም የጨዋታ ችግር ካጋጠምዎ ድጋፍን ያነጋግሩ።",
            reply_markup=main_keyboard(),
        )


# ============================================================
# ERROR HANDLER / BOT COMMANDS
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        "Telegram bot encountered an error",
        exc_info=context.error,
    )


async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Open Guder Bingo / ጉደር ቢንጎ"),
        BotCommand("app", "Open the game menu / የጨዋታ ማውጫ"),
        BotCommand("cancel", "Cancel receipt submission / ማቋረጥ"),
    ])


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    if not BOT_API_SECRET:
        raise RuntimeError("BOT_API_SECRET environment variable is missing.")

    if not APP_URL.startswith("https://"):
        raise RuntimeError("APP_URL must be a valid HTTPS address.")

    if not BACKEND_URL.startswith("https://"):
        raise RuntimeError("BACKEND_URL must be a valid HTTPS address.")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("app", open_app))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_receipt)
    )
    application.add_error_handler(error_handler)

    logger.info("Guder Bingo Telegram bot is starting.")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
