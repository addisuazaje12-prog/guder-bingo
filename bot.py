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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


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
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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

        "Your submitted receipt will be processed by the system.\n\n"

        "Eligible submissions may receive PROVISIONAL playing "
        "credit so that you can proceed to cartela selection "
        "without waiting for manager approval.\n\n"

        "⚠️ Provisional credit is subject to independent "
        "payment verification by the manager.\n\n"

        "Send your complete receipt now.\n"

        "Send /cancel if you want to stop."
    )

    await update.effective_message.reply_text(message)


# ============================================================
# CANCEL RECEIPT SUBMISSION
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

            "Please press Deposit first, then send your "
            "complete TeleBirr receipt.",

            reply_markup=main_keyboard()
        )

        return

    if len(receipt) < 10:

        await message.reply_text(
            "❌ The receipt appears incomplete.\n\n"

            "Please copy and send the complete TeleBirr "
            "transaction receipt."
        )

        return

    if not BOT_API_SECRET:

        logger.error("BOT_API_SECRET is missing.")

        await message.reply_text(
            "❌ Deposit service configuration error.\n\n"
            "Please contact Guder Bingo support."
        )

        return

    processing_message = await message.reply_text(
        "⏳ Your TeleBirr receipt has been received.\n\n"
        "Please wait while the system processes it."
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
                "Backend returned non-JSON response: %s",
                response.text[:1000]
            )

            await processing_message.edit_text(
                "❌ The deposit service returned an unexpected "
                "response.\n\n"

                "Your receipt has not been confirmed. "
                "Please try again later or contact support."
            )

            return

        logger.info(
            "Deposit response: user=%s HTTP=%s success=%s",
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

            await processing_message.edit_text(
                "❌ DEPOSIT NOT PROCESSED\n\n"

                f"{error}\n\n"

                "Please check your receipt and try again."
            )

            context.user_data["waiting_for_receipt"] = True

            return

        amount = data.get("amount")
        reference = data.get("reference", "")

        status = str(
            data.get("status", "")
        ).upper()

        # Do not tell the player that credit was recorded
        # unless the backend confirms the provisional status.

        if status != "PROVISIONAL":

            logger.warning(
                "Unexpected deposit status: %s",
                status
            )

            await processing_message.edit_text(
                "⚠️ Your receipt was received by the server, "
                "but provisional playing credit was not confirmed.\n\n"

                "Please contact Guder Bingo support before "
                "submitting the receipt again."
            )

            context.user_data["waiting_for_receipt"] = False

            return

        if not reference or amount is None:

            logger.error(
                "Backend response missing amount or reference."
            )

            await processing_message.edit_text(
                "❌ The server response is incomplete.\n\n"
                "Please contact Guder Bingo support."
            )

            return

        context.user_data["waiting_for_receipt"] = False

        await processing_message.edit_text(

            "✅ RECEIPT SUBMITTED SUCCESSFULLY\n\n"

            f"Amount: {amount} ETB\n"

            f"Transaction number: {reference}\n\n"

            "Status: PROVISIONAL\n\n"

            "Your provisional playing credit has been recorded.\n\n"

            "🎮 Open Guder Bingo now and select an available "
            "cartela during the selection period.\n\n"

            "The manager will independently review the payment.",

            reply_markup=main_keyboard()
        )

    except requests.exceptions.Timeout:

        logger.exception("Deposit request timed out.")

        await processing_message.edit_text(
            "⏳ The deposit service did not respond in time.\n\n"

            "Your receipt may already have been processed. "
            "Please check your wallet before submitting it again."
        )

        context.user_data["waiting_for_receipt"] = True

    except requests.exceptions.RequestException:

        logger.exception("Deposit connection failed.")

        await processing_message.edit_text(
            "❌ Could not connect to the deposit service.\n\n"

            "Please try again later."
        )

        context.user_data["waiting_for_receipt"] = True

    except Exception:

        logger.exception("Unexpected receipt processing error.")

        await processing_message.edit_text(
            "❌ An unexpected error occurred while processing "
            "your receipt.\n\n"

            "Please contact Guder Bingo support."
        )

        context.user_data["waiting_for_receipt"] = True


# ============================================================
# BUTTON HANDLER
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

            "Open Guder Bingo Mini App to see your latest "
            "wallet balance.\n\n"

            "If you have just submitted a deposit receipt, "
            "refresh the Mini App to update your balance.",

            reply_markup=main_keyboard()
        )

    elif query.data == "history":

        await query.message.reply_text(
            "🏆 Open Guder Bingo and select History "
            "to view your game results.",

            reply_markup=main_keyboard()
        )

    elif query.data == "invite":

        link = (
            f"https://t.me/guderbingo_bot?start=ref_{user_id}"
        )

        await query.message.reply_text(
            "📤 INVITE FRIENDS TO GUDER BINGO\n\n"

            "Share this link with your friends:\n\n"

            f"{link}",

            reply_markup=main_keyboard()
        )

    elif query.data == "instructions":

        instructions = (
            "📖 HOW TO PLAY GUDER BINGO\n\n"

            "1. Press Deposit.\n"

            "2. Transfer money using TeleBirr.\n"

            "3. Copy and submit your complete receipt.\n"

            "4. After successful provisional credit, "
            "open the Mini App.\n"

            "5. Select an available cartela during the "
            "selection period.\n"

            "6. Watch the numbers being called.\n"

            "7. Press BINGO when your card has a valid "
            "winning pattern.\n\n"

            "The server checks every BINGO claim."
        )

        await query.message.reply_text(
            instructions,
            reply_markup=main_keyboard()
        )

    elif query.data == "support":

        await query.message.reply_text(
            "☎ GUDER BINGO SUPPORT\n\n"

            f"Support contact: {SUPPORT_NUMBER}\n\n"

            "Please contact support if your receipt "
            "cannot be processed.",

            reply_markup=main_keyboard()
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram bot encountered an error:",
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if not BOT_API_SECRET:
        raise RuntimeError(
            "BOT_API_SECRET environment variable is missing."
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

    logger.info("Guder Bingo Telegram bot is starting...")

    application.run_polling(
        drop_pending_updates=False
    )


# ============================================================
# RUN BOT
# ============================================================

if __name__ == "__main__":
    main()
