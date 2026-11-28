import os
import logging
import asyncio
import requests

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ============================================================
# GUDER BINGO TELEGRAM BOT
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

APP_URL = os.environ.get(
    "APP_URL",
    "https://guder-bingo.ethiodeploy.app"
).rstrip("/")

BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    APP_URL
).rstrip("/")

BOT_API_SECRET = os.environ.get(
    "BOT_API_SECRET",
    ""
).strip()

TELEBIRR_NUMBER = os.environ.get(
    "TELEBIRR_NUMBER",
    "0913706670"
).strip()

SUPPORT_NUMBER = os.environ.get(
    "SUPPORT_NUMBER",
    "0913706670"
).strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger("guder-bingo-bot")


# ============================================================
# MAIN MENU
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎮 Play a Game",
                web_app=WebAppInfo(url=APP_URL)
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Deposit",
                callback_data="deposit"
            ),
            InlineKeyboardButton(
                "💳 Balance",
                callback_data="balance"
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 History",
                callback_data="history"
            ),
            InlineKeyboardButton(
                "📤 Invite Friends",
                callback_data="invite"
            )
        ],
        [
            InlineKeyboardButton(
                "📖 Instructions",
                callback_data="instructions"
            ),
            InlineKeyboardButton(
                "☎ Support",
                callback_data="support"
            )
        ]
    ])


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_for_receipt"] = False

    welcome = (
        "🎉 እንኳን ወደ ጉደር ቢንጎ በሰላም መጡ!\n\n"
        "Welcome to Guder Bingo!\n\n"
        "Choose an option below to continue.\n\n"
        "💰 Deposit before selecting your cartela.\n"
        "🎮 You can watch the live game without a balance.\n\n"
        "To submit a payment receipt, press Deposit first."
    )

    await update.effective_message.reply_text(
        welcome,
        reply_markup=main_keyboard()
    )


# ============================================================
# DEPOSIT INSTRUCTIONS
# ============================================================

async def deposit_instructions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["waiting_for_receipt"] = True

    message = (
        "💰 GUDER BINGO DEPOSIT\n\n"
        f"TeleBirr number: {TELEBIRR_NUMBER}\n\n"
        "1. Transfer your payment using TeleBirr.\n"
        "2. Open your TeleBirr transaction receipt.\n"
        "3. Copy the complete receipt text.\n"
        "4. Return here and send the receipt as a text message.\n\n"
        "After the system reads the payment amount and transaction "
        "number, playing credit is added immediately.\n\n"
        "🎮 You can then open Guder Bingo and select an available "
        "cartela during the selection period.\n\n"
        "Send your complete receipt now. Send /cancel to stop."
    )

    await update.effective_message.reply_text(message)


# ============================================================
# CANCEL
# ============================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["waiting_for_receipt"] = False

    await update.effective_message.reply_text(
        "Receipt submission cancelled.\n\n"
        "You can start again by pressing Deposit.",
        reply_markup=main_keyboard()
    )


# ============================================================
# RECEIPT SUBMISSION
# ============================================================

async def receive_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    message = update.effective_message

    if not message or not message.text or not user:
        return

    receipt = message.text.strip()

    if not context.user_data.get("waiting_for_receipt"):
        await message.reply_text(
            "I received your message, but you have not started "
            "the deposit process.\n\n"
            "Please press Deposit first, then send your complete "
            "TeleBirr receipt.",
            reply_markup=main_keyboard()
        )
        return

    if len(receipt) < 10:
        await message.reply_text(
            "❌ The receipt appears incomplete. Please copy and "
            "send the complete TeleBirr transaction receipt."
        )
        return

    if not BOT_API_SECRET:
        logger.error("BOT_API_SECRET is missing")

        await message.reply_text(
            "❌ Deposit service configuration error. "
            "Please contact support."
        )
        return

    processing = await message.reply_text(
        "⏳ Your TeleBirr receipt has been received. "
        "The system is processing it..."
    )

    payload = {
        "user_id": str(user.id),
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username or "",
        "receipt": receipt
    }

    headers = {
        "X-Bot-Secret": BOT_API_SECRET,
        "Content-Type": "application/json"
    }

    try:
        response = await asyncio.to_thread(
            requests.post,
            f"{BACKEND_URL}/api/bot/deposit",
            json=payload,
            headers=headers,
            timeout=30
        )

        try:
            data = response.json()
        except ValueError:
            logger.error(
                "Backend non-JSON response HTTP %s: %s",
                response.status_code,
                response.text[:500]
            )

            await processing.edit_text(
                "❌ The deposit service returned an unexpected "
                "response. Your receipt has not been processed. "
                "Please try again later or contact support."
            )
            return

        logger.info(
            "Deposit response user=%s HTTP=%s success=%s",
            user.id,
            response.status_code,
            data.get("success")
        )

        if not (200 <= response.status_code < 300) or not data.get("success"):
            error = data.get(
                "error",
                data.get(
                    "message",
                    "The receipt could not be processed."
                )
            )

            await processing.edit_text(
                f"❌ DEPOSIT NOT PROCESSED\n\n"
                f"{error}\n\n"
                "Please check your receipt and try again."
            )

            context.user_data["waiting_for_receipt"] = True
            return

        amount = data.get("amount")
        reference = data.get("reference", "")
        status = str(data.get("status", "")).upper()

        if status != "PROVISIONAL" or not reference or amount is None:
            logger.warning(
                "Unexpected deposit response: %r",
                data
            )

            await processing.edit_text(
                "⚠️ The server did not confirm the playing credit. "
                "Please contact support before submitting the "
                "same receipt again."
            )

            context.user_data["waiting_for_receipt"] = False
            return

        context.user_data["waiting_for_receipt"] = False

        await processing.edit_text(
            "✅ RECEIPT SUBMITTED SUCCESSFULLY!\n\n"
            f"Amount: {amount} ETB\n"
            f"Transaction number: {reference}\n\n"
            "💰 Your playing credit has been added.\n\n"
            "🎮 You can now open Guder Bingo and select an "
            "available cartela during the selection period.\n\n"
            "Enjoy playing Guder Bingo!",
            reply_markup=main_keyboard()
        )

    except requests.exceptions.Timeout:
        logger.exception("Deposit request timed out")

        await processing.edit_text(
            "⏳ The deposit service did not respond in time. "
            "Your receipt may already have been processed. "
            "Please check your wallet before submitting it again."
        )

        context.user_data["waiting_for_receipt"] = False

    except requests.exceptions.RequestException:
        logger.exception("Deposit connection failed")

        await processing.edit_text(
            "❌ Could not connect to the deposit service. "
            "Please try again later."
        )

        context.user_data["waiting_for_receipt"] = True

    except Exception:
        logger.exception("Unexpected receipt processing error")

        await processing.edit_text(
            "❌ An unexpected error occurred. "
            "Please contact support."
        )

        context.user_data["waiting_for_receipt"] = True


# ============================================================
# INLINE BUTTONS
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    user_id = update.effective_user.id

    if query.data == "deposit":
        await deposit_instructions(update, context)

    elif query.data == "balance":
        await query.message.reply_text(
            "💳 WALLET BALANCE\n\n"
            "Open Guder Bingo Mini App to see your latest wallet "
            "balance. Refresh the Mini App after submitting a receipt.",
            reply_markup=main_keyboard()
        )

    elif query.data == "history":
        await query.message.reply_text(
            "🏆 Open Guder Bingo and select History to view game results.",
            reply_markup=main_keyboard()
        )

    elif query.data == "invite":
        link = (
            f"https://t.me/guderbingo_bot"
            f"?start=ref_{user_id}"
        )

        await query.message.reply_text(
            "📤 INVITE FRIENDS TO GUDER BINGO\n\n"
            "Share this link:\n\n"
            f"{link}",
            reply_markup=main_keyboard()
        )

    elif query.data == "instructions":
        await query.message.reply_text(
            "📖 HOW TO PLAY GUDER BINGO\n\n"
            "1. Press Deposit.\n"
            "2. Transfer using TeleBirr.\n"
            "3. Submit the complete receipt text.\n"
            "4. Your playing credit is added after the receipt "
            "is successfully processed.\n"
            "5. Open the Mini App.\n"
            "6. Select an available cartela during the "
            "30-second selection period.\n"
            "7. Watch the called numbers.\n"
            "8. Press BINGO when your card has a valid winning pattern.\n\n"
            "Enjoy the game!",
            reply_markup=main_keyboard()
        )

    elif query.data == "support":
        await query.message.reply_text(
            "☎ GUDER BINGO SUPPORT\n\n"
            f"Support contact: {SUPPORT_NUMBER}\n\n"
            "Contact support if your receipt cannot be processed.",
            reply_markup=main_keyboard()
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    logger.error(
        "Telegram bot encountered an error",
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing"
        )

    if not BOT_API_SECRET:
        raise RuntimeError(
            "BOT_API_SECRET environment variable is missing"
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("cancel", cancel)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_receipt
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Guder Bingo Telegram bot is starting")

    application.run_polling(
        drop_pending_updates=False
    )


if __name__ == "__main__":
    main()
