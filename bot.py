import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7488313000:AAHYourActualBotTokenHere...") 
WEBAPP_URL = os.environ.get("WEBAPP_URL", "http://127.0.0.1:5000")
TELEBIRR_ACCOUNT = "0913706670"
CBE_ACCOUNT = "1000000000000"
MANAGER_CHAT_ID = int(os.environ.get("MANAGER_CHAT_ID", "0"))

# Conversation states for Deposit
AWAITING_AMOUNT, SELECTING_PAYMENT, AWAITING_RECEIPT = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"👋 Welcome to Guder Bingo, {user.first_name}!\n\n"
        f"💰 Wallet Balance: **0.00 ETB**\n"
        f"⚠️ Minimum balance of 10.00 ETB required to play.\n\n"
        f"Choose an option below to get started:"
    )
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Game", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton("💳 Deposit", callback_data="menu_deposit"), InlineKeyboardButton("💸 Withdraw", callback_data="menu_withdraw")],
        [InlineKeyboardButton("📊 Balance", callback_data="menu_balance")],
        [InlineKeyboardButton("📞 Support", callback_data="menu_support"), InlineKeyboardButton("📜 Instructions", callback_data="menu_instructions")],
        [InlineKeyboardButton("👥 Invite Friends", callback_data="menu_invite")],
        [InlineKeyboardButton("⚙️ Manager Controls", callback_data="menu_manager")]
    ]
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# --- DEPOSIT FLOW ---
async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💳 **Deposit Request**\n\nEnter the amount you wish to deposit (Minimum 10 ETB):",
        parse_mode="Markdown"
    )
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
            [InlineKeyboardButton("📱 Telebirr (ቴሌብር)", callback_data="pay_telebirr")],
            [InlineKeyboardButton("🏦 CBE Birr (የኢትዮጵያ ንግድ ባንክ)", callback_data="pay_cbe")]
        ]
        await update.message.reply_text(
            f"✅ Amount: **{amount:.2f} ETB**\n\n"
            f"እባክዎ የሚከፍሉበትን የክፍያ አማራጭ ይምረጡ (Select payment method):", 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode="Markdown"
        )
        return SELECTING_PAYMENT
    except ValueError:
        await update.message.reply_text("❌ እባክዎ ትክክለኛ የብር መጠን ያስገቡ (Please enter a valid number):")
        return AWAITING_AMOUNT

async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = context.user_data.get("deposit_amount", 10.0)
    is_telebirr = query.data == "pay_telebirr"
    method = "Telebirr" if is_telebirr else "CBE Birr"
    acc_num = TELEBIRR_ACCOUNT if is_telebirr else CBE_ACCOUNT
    
    # Copiable account block using markdown code backticks
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
    receipt_text = update.message.text
    amount = context.user_data.get("deposit_amount", 0.0)
    user = update.effective_user
    
    await update.message.reply_text(
        "✅ **ደረሰኝዎ ተቀብሏል!** (Receipt received!)\n"
        "በአስተዳዳሪው ከተረጋገጠ በኋላ የኪስ ቦርሳዎ ይሞላል።",
        parse_mode="Markdown"
    )
    
    # Forward to Manager if configured
    if MANAGER_CHAT_ID != 0:
        try:
            await context.bot.send_message(
                chat_id=MANAGER_CHAT_ID,
                text=f"🔔 **New Deposit Receipt**\nUser: @{user.username} ({user.id})\nAmount: {amount} ETB\n\nReceipt:\n{receipt_text}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify manager: {e}")
            
    return ConversationHandler.END

async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Deposit cancelled.")
    return ConversationHandler.END

# --- OTHER MENU HANDLERS ---
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "menu_balance":
        await query.edit_message_text("💰 Wallet Balance: **0.00 ETB**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]))
    elif data == "menu_withdraw":
        await query.edit_message_text("💸 Withdrawal feature is available through your profile wallet.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]))
    elif data == "menu_support":
        await query.edit_message_text("📞 Support: Contact @Guderbingosupport for assistance.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]))
    elif data == "menu_instructions":
        await query.edit_message_text("📜 **How to play:**\n1. Deposit funds.\n2. Open Mini App & pick a cartela (1-800).\n3. Mark numbers and win!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]))
    elif data == "menu_invite":
        await query.edit_message_text("👥 Share your bot link with friends to invite them to Guder Bingo!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]))
    elif data == "menu_manager":
        await query.edit_message_text("📊 Manager Control Panel active.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]]))
    elif data == "menu_back":
        await start(update, context)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    deposit_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_deposit, pattern="^menu_deposit$")],
        states={
            AWAITING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_deposit_amount)],
            SELECTING_PAYMENT: [CallbackQueryHandler(handle_payment_choice, pattern="^pay_")],
            AWAITING_RECEIPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receipt)],
        },
        fallbacks=[CommandHandler("cancel", cancel_deposit)]
    )
    
    app.add_handler(deposit_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))
    
    print("🤖 Guder Bingo Telegram Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
