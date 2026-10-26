import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# ==========================================
# 1. CONFIGURATION & COMMISSION SETTINGS
# ==========================================
WEB_APP_URL = "https://addisuazaje12-prog.github.io/guder-bingo/  # Active web app URL
CARD_PRICE = 10.0                            # Minimum stake / card price in ETB
MANAGER_COMMISSION_RATE = 0.50               # 50% Manager Commission (Addisu Azaje)

# Telebirr Deposit Information
TELEBIRR_NUMBER = "0913706670"
SUPPORT_USERNAME = "@Guderbingosupport"

# ==========================================
# 2. AUTOMATED PAYOUT ENGINE (50% / 50%)
# ==========================================
def calculate_payouts(total_cards_sold: int):
    """Calculates 50% manager fee and 50% winner prize pool."""
    gross_pool = total_cards_sold * CARD_PRICE
    manager_fee = gross_pool * MANAGER_COMMISSION_RATE
    winner_prize = gross_pool - manager_fee
    
    return {
        "gross_pool": gross_pool,      # Total ETB collected
        "manager_fee": manager_fee,    # 50% to Manager (Addisu Azaje)
        "winner_prize": winner_prize   # 50% to Winner Pool
    }

# ==========================================
# 3. BOT COMMAND HANDLERS
# ==========================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command and presents main keyboard buttons."""
    keyboard = [
        [InlineKeyboardButton("🎮 Play Game", web_app=WebAppInfo(url=WEB_APP_URL))],
        [
            InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
            InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")
        ],
        [
            InlineKeyboardButton("📞 Support", url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}"),
            InlineKeyboardButton("📖 Instructions", callback_data="instructions")
        ],
        [InlineKeyboardButton("🎁 Invite Friends", callback_data="invite")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        "Welcome to **Guder Bingo**! 🎯\n\n"
        "To play, ensure you have at least 10.00 ETB in your wallet.\n"
        "Use the menu below or the menu button on the bottom left to navigate."
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /register."""
    user = update.effective_user
    await update.message.reply_text(f"Welcome {user.first_name}! Your account has been registered for Guder Bingo.")

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /deposit flow."""
    deposit_text = (
        "💳 **Deposit Funds**\n\n"
        "Please enter the amount you wish to deposit (Minimum 10 ETB):\n"
        "Type the amount in chat (e.g., `50` or `100`)."
    )
    await update.message.reply_text(deposit_text, parse_mode="Markdown")

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /play to open Web App."""
    keyboard = [[
        InlineKeyboardButton("🎮 Open Guder Bingo App", web_app=WebAppInfo(url=WEB_APP_URL))
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Click below to claim your cartela and join the live game:", reply_markup=reply_markup)

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /withdraw."""
    await update.message.reply_text("💸 **Withdrawal Request**\n\nEnter the amount you wish to withdraw to your Telebirr account:")

async def instruction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /instruction."""
    instructions = (
        "📖 **Guder Bingo Game Rules**\n\n"
        "1. Minimum deposit is 10 ETB.\n"
        "2. Players have **30 seconds** during registration to select their cartela (up to 800 cards).\n"
        "3. Numbers are called out visually and by voice.\n"
        "4. Tap **Bingo!** when you complete a winning line.\n"
        "5. The winner takes **50%** of the pool, while **50%** goes to the manager."
    )
    await update.message.reply_text(instructions, parse_mode="Markdown")

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /support."""
    await update.message.reply_text(f"📞 For support or help with deposits, contact our official channel: {SUPPORT_USERNAME}")

async def get_invitation_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /get_invitation_link."""
    user_id = update.effective_user.id
    ref_link = f"https://t.me/guderbingo_bot?start={user_id}"
    await update.message.reply_text(f"🎁 Here is your unique invitation link:\n`{ref_link}`", parse_mode="Markdown")

async def register_as_agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /register_as_agent."""
    await update.message.reply_text("💼 **Agent Registration**: Your request to become an agent has been submitted to manager Addisu Azaje.")

async def invite_sub_agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /invite_sub_agent."""
    await update.message.reply_text("👥 Use `/get_invitation_link` to invite sub-agents and earn commissions on their play volume.")

async def sale_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /sale (Admin/Manager sales review)."""
    await update.message.reply_text("📊 **Sales & Commission Report**\n\nManager Share: 50%\nWinner Pool: 50%")

# Handler for processing SMS receipts or Deposit amounts typed into chat
async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # Simple check if player typed a numeric deposit amount
    if text.isdigit() and int(text) >= 10:
        amount = int(text)
        instructions = (
            f"📱 **Tele-Birr Account:** `{TELEBIRR_NUMBER}`\n\n"
            "**Telebirr Instructions:**\n"
            f"1. Transfer **{amount}.00 ETB** to the account above via Telebirr.\n"
            "2. Copy the entire SMS confirmation message you receive from Telebirr.\n"
            "3. Paste the entire SMS message back into this chat window to complete deposit.\n\n"
            f"📞 Need help? Contact {SUPPORT_USERNAME}"
        )
        await update.message.reply_text(instructions, parse_mode="Markdown")
    # Check if user pasted a Telebirr SMS receipt
    elif "transaction" in text.lower() or "telebirr" in text.lower() or "completed" in text.lower():
        await update.message.reply_text("✅ Receipt received! Your wallet is being credited. You can now use `/play` to join the game.")
    else:
        await update.message.reply_text("Type `/play` to launch the game or `/deposit` to add funds.")

# Setup Bot Commands for Telegram Menu Button (Bottom-Left)
async def post_init(application):
    commands = [
        BotCommand("register", "Register your account"),
        BotCommand("deposit", "Deposit money via Telebirr"),
        BotCommand("play", "Start playing Guder Bingo"),
        BotCommand("withdraw", "Withdraw your winnings"),
        BotCommand("instruction", "Game rules and guide"),
        BotCommand("support", "Contact support center"),
        BotCommand("get_invitation_link", "Get referral link"),
        BotCommand("register_as_agent", "Register as agent"),
        BotCommand("invite_sub_agent", "Invite sub-agents"),
        BotCommand("sale", "Sales & commission logs")
    ]
    await application.bot.set_my_commands(commands)

# ==========================================
# 4. MAIN BOT EXECUTION
# ==========================================
if __name__ == '__main__':
    # Insert your actual BotFather token string here
    BOT_TOKEN = "8813599415:AAGgwpsZzL4iaG0Sq-YqFlQ9rliwTzxAsPA"
    
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("register", register_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("play", play_command))
    app.add_handler(CommandHandler("withdraw", withdraw_command))
    app.add_handler(CommandHandler("instruction", instruction_command))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CommandHandler("get_invitation_link", get_invitation_link_command))
    app.add_handler(CommandHandler("register_as_agent", register_as_agent_command))
    app.add_handler(CommandHandler("invite_sub_agent", invite_sub_agent_command))
    app.add_handler(CommandHandler("sale", sale_command))
    
    # Register message handler for deposit amounts & SMS receipt pasting
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_input))
    
    print("Starting Guder Bingo Bot...")
    app.run_polling()
