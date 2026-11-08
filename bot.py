"""
Guder Bingo Telegram Bot
------------------------
This bot is the Telegram entry point for the Flask application.

Important:
- Flask is the source of truth for wallet balances, deposits and games.
- This bot does NOT automatically credit deposits or pay winnings.
- Players submit receipts through the Web App.
- Deposits remain pending until an administrator verifies payment.
- Never hard-code your Telegram bot token in this file.
"""

import logging
import os
from urllib.parse import quote

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Must be the public HTTPS address of your Flask application.
WEB_APP_URL = os.getenv("WEB_APP_URL", "").strip().rstrip("/")

SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "Guderbingosupport"
).lstrip("@")

BOT_USERNAME = os.getenv(
    "BOT_USERNAME",
    "guderbingo_bot"
).lstrip("@")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("guder_bingo_bot")


# ============================================================
# KEYBOARDS
# ============================================================

def web_app_button(label: str, url: str):

    return InlineKeyboardButton(
        label,
        web_app=WebAppInfo(url=url)
    )


def main_menu():

    return InlineKeyboardMarkup([

        [
            web_app_button(
                "🎮 PLAY GAME",
                WEB_APP_URL
            )
        ],

        [
            web_app_button(
                "💳 DEPOSIT / MY WALLET",
                WEB_APP_URL
            )
        ],

        [
            InlineKeyboardButton(
                "📖 Instructions",
                callback_data="instructions"
            ),

            InlineKeyboardButton(
                "📞 Support",
                callback_data="support"
            )
        ],

        [
            InlineKeyboardButton(
                "🎁 Invite Friends",
                callback_data="invite"
            )
        ]

    ])


# ============================================================
# WEB APP VALIDATION
# ============================================================

def valid_web_app_url():

    return WEB_APP_URL.startswith("https://")


async def send_web_app_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    heading: str,
    detail: str,
):

    message = update.effective_message

    if not message:
        return

    if not valid_web_app_url():

        await message.reply_text(

            "The game is not configured yet.\n\n"
            "The manager must set WEB_APP_URL to the "
            "public HTTPS address of the Flask application."

        )

        return

    await message.reply_text(

        f"{heading}\n\n{detail}",

        reply_markup=main_menu()

    )


# ============================================================
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not update.effective_message:
        return

    # Optional referral parameter.
    referral = context.args[0] if context.args else None

    if referral and referral.startswith("ref_"):

        context.user_data["referral_code"] = referral[4:]

    if not valid_web_app_url():

        await update.effective_message.reply_text(

            "👋 Welcome to Guder Bingo!\n\n"

            "The bot is running, but the game URL "
            "has not been configured yet.\n\n"

            "Please contact the manager."

        )

        return

    await update.effective_message.reply_text(

        f"👋 Welcome to Guder Bingo, {user.first_name}!\n\n"

        "Use the buttons below to:\n\n"

        "🎮 Play Bingo\n"
        "💳 Deposit money\n"
        "👛 Check your wallet\n"
        "📜 View game history\n"
        "🎁 Invite friends\n\n"

        "🔐 Your wallet and game records are managed "
        "by the secure game server.\n\n"

        "A submitted payment receipt is not credited "
        "until the payment has been independently verified.",

        reply_markup=main_menu()

    )


# ============================================================
# PLAY COMMAND
# ============================================================

async def play(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_web_app_link(

        update,
        context,

        "🎮 Guder Bingo",

        "Open the game to see the current room "
        "and select a cartela when registration is open."

    )


# ============================================================
# DEPOSIT COMMAND
# ============================================================

async def deposit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_web_app_link(

        update,
        context,

        "💳 Deposit",

        "Open the Guder Bingo Web App.\n\n"

        "1. Select Deposit.\n"
        "2. Choose Telebirr or CBE.\n"
        "3. Make your payment.\n"
        "4. Submit your transaction receipt.\n"
        "5. Wait for payment verification."

    )


# ============================================================
# WALLET COMMAND
# ============================================================

async def wallet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_web_app_link(

        update,
        context,

        "👛 My Wallet",

        "Open the Web App to view your current "
        "wallet balance and transaction history."

    )


# ============================================================
# HISTORY COMMAND
# ============================================================

async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_web_app_link(

        update,
        context,

        "📜 Game History",

        "Open the Web App and select History "
        "to view your recorded games."

    )


# ============================================================
# HELP COMMAND
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_message:
        return

    await update.effective_message.reply_text(

        "🎮 GUDER BINGO — HELP\n\n"

        "/start — Open the main menu\n"
        "/play — Open the game\n"
        "/deposit — Submit a deposit receipt\n"
        "/wallet — View your wallet\n"
        "/balance — Check your wallet\n"
        "/history — View game history\n"
        "/support — Contact support\n"
        "/cancel — Cancel interaction\n\n"

        "A receipt submission is only a request. "
        "Funds are added after independent payment verification."

    )


# ============================================================
# SUPPORT COMMAND
# ============================================================

async def support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_message:
        return

    support_url = f"https://t.me/{SUPPORT_USERNAME}"

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📞 Contact Guder Bingo Support",
                url=support_url
            )
        ]

    ])

    await update.effective_message.reply_text(

        f"📞 Support: @{SUPPORT_USERNAME}",

        reply_markup=keyboard

    )


# ============================================================
# CANCEL COMMAND
# ============================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_message:

        await update.effective_message.reply_text(

            "No active bot form needs cancelling.\n\n"
            "Use /start to open the main menu."

        )


# ============================================================
# MENU CALLBACKS
# ============================================================

async def menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    # INSTRUCTIONS
    if query.data == "instructions":

        await query.message.reply_text(

            "📖 GUDER BINGO INSTRUCTIONS\n\n"

            "1. Open the game using PLAY GAME.\n"
            "2. Submit a deposit receipt.\n"
            "3. Wait for payment verification.\n"
            "4. Select an available cartela.\n"
            "5. Watch the called numbers.\n"
            "6. Your card is marked automatically.\n"
            "7. Claim Bingo when you complete a valid pattern.\n\n"

            "STANDARD 75-BALL BINGO NUMBERS\n\n"

            "B: 1–15\n"
            "I: 16–30\n"
            "N: 31–45\n"
            "G: 46–60\n"
            "O: 61–75\n\n"

            "Never share your Telegram login code "
            "or account password."

        )

    # SUPPORT
    elif query.data == "support":

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "Contact Support",
                    url=f"https://t.me/{SUPPORT_USERNAME}"
                )
            ]

        ])

        await query.message.reply_text(

            f"Contact Guder Bingo support: @{SUPPORT_USERNAME}",

            reply_markup=keyboard

        )

    # INVITE FRIENDS
    elif query.data == "invite":

        user = query.from_user

        referral_link = (
            f"https://t.me/{BOT_USERNAME}?start=ref_{user.id}"
        )

        share_link = (

            "https://t.me/share/url?url="
            + quote(referral_link, safe="")
            + "&text="
            + quote("Join me on Guder Bingo!", safe="")

        )

        keyboard = InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "🎁 Share Invitation",
                    url=share_link
                )
            ]

        ])

        await query.message.reply_text(

            "🎁 Invite your friends to Guder Bingo!\n\n"
            f"{referral_link}",

            reply_markup=keyboard

        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Unhandled bot error",
        exc_info=context.error
    )


# ============================================================
# MAIN APPLICATION
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(

            "BOT_TOKEN is missing.\n"
            "Set BOT_TOKEN as an environment variable.\n"
            "Do not paste your private token into bot.py."

        )

    if not valid_web_app_url():

        logger.warning(

            "WEB_APP_URL is missing or is not HTTPS. "
            "The bot can start, but Web App buttons will not work."

        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # COMMAND HANDLERS

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("play", play)
    )

    application.add_handler(
        CommandHandler("deposit", deposit)
    )

    application.add_handler(
        CommandHandler("wallet", wallet)
    )

    application.add_handler(
        CommandHandler("balance", wallet)
    )

    application.add_handler(
        CommandHandler("history", history)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("support", support)
    )

    application.add_handler(
        CommandHandler("cancel", cancel)
    )

    # BUTTON HANDLERS

    application.add_handler(

        CallbackQueryHandler(

            menu_callback,

            pattern=r"^(instructions|support|invite)$"

        )

    )

    # ERROR HANDLER

    application.add_error_handler(error_handler)

    logger.info("Guder Bingo Telegram bot is starting.")

    # START BOT

    application.run_polling(

        allowed_updates=Update.ALL_TYPES,

        drop_pending_updates=False

    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
