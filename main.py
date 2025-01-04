#main.py
#main.py
import os
import logging
import sys
import signal
import telebot
from dotenv import load_dotenv
from pathlib import Path
from payment_handler import PaymentHandler
from call_utils import CallUtils
from twilio_handler import TwilioHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)

def load_environment():
    env_path = Path('.env')
    load_dotenv(dotenv_path=env_path)

    required_vars = [
        'TELEGRAM_BOT_TOKEN',
        'TWILIO_ACCOUNT_SID',
        'TWILIO_AUTH_TOKEN',
        'TWILIO_PHONE_NUMBER',
        'TWILIO_FUNCTION_URL',
        'REDIS_URL'
    ]

    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
            logging.error(f"{var} not found in environment variables!")

    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return False

    logging.info("All required environment variables loaded successfully")
    return True

if not load_environment():
    logging.error("Failed to load required environment variables. Exiting...")
    sys.exit(1)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Initialize handlers
payment_handler = PaymentHandler(bot)
payment_handler.add_payment_handlers()
call_utils = CallUtils()
twilio_handler = TwilioHandler(bot, call_utils)

# User state storage
user_states = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "🎯 Welcome to *OneCaller*!\n\n"
        "I can help you verify phone numbers through voice calls.\n\n"
        "🏦 This service is designed to enhance account security by delivering OTP codes via a secure voice call\n\n"
        "📱 Features:\n"
        "• Secure voice call verification\n"
        "• Real-time call status updates\n"
        "• Automatic OTP collection\n\n"
        "Select an option to begin:"
    )
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode="Markdown",
        reply_markup=payment_handler.create_main_menu_keyboard()
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    chat_id = call.message.chat.id

    if call.data == "start_verification":
        if not payment_handler.handle_start_verification(call):
            return
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            "👤 Enter the name of the call recipient:"
        )
        user_states[chat_id] = "awaiting_recipient_name"
    elif call.data == "return_menu":
        bot.answer_callback_query(call.id)
        send_welcome(call.message)
    elif call.data == "show_plans":
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            "💎 Choose a subscription plan:",
            reply_markup=payment_handler.create_subscription_keyboard()
        )
    elif call.data.startswith("verify_") or call.data.startswith("hangup_") or call.data.startswith("cancel_call_"):
        twilio_handler.handle_call_callbacks(call)
    elif call.data.startswith("bank_") or call.data.startswith("banks_page_") or call.data == "custom_bank":
        call_utils.handle_bank_selection(call, bot, user_states)
    elif call.data in ["help", "status"]:
        call_utils.handle_utility_callbacks(call, bot)

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    chat_id = message.chat.id

    if chat_id not in user_states:
        bot.send_message(
            chat_id,
            "⚠️ Please use /start to begin verification.",
            reply_markup=payment_handler.create_main_menu_keyboard()
        )
        return

    # Add this block for custom bank handling
    if isinstance(user_states[chat_id], dict) and user_states[chat_id].get("state") == "awaiting_custom_bank":
        if call_utils.handle_message(message, bot, user_states):
            return

    # Rest of your original handle_messages function remains exactly the same
    if user_states[chat_id] == "awaiting_recipient_name":
        user_states[chat_id] = {"state": "awaiting_bank", "recipient_name": message.text.strip()}
        bot.send_message(chat_id, "🏦 Select the banking institution:", reply_markup=call_utils.create_bank_keyboard())
    elif isinstance(user_states[chat_id], dict):
        if user_states[chat_id].get("state") == "awaiting_bank":
            bot.send_message(chat_id, "🏦 Select the banking institution:", reply_markup=call_utils.create_bank_keyboard())
        elif user_states[chat_id].get("state") == "awaiting_phone":
            twilio_handler.handle_phone_number(message, user_states[chat_id].get("recipient_name"), 
                                            user_states[chat_id].get("bank"), None)

def signal_handler(signum, frame):
    logging.info("Shutting down bot...")
    print("\n👋 Bot shutdown requested. Cleaning up...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    logging.info("Starting bot polling...")
    bot.infinity_polling()
