#payment_handler.py
import os
import time
import json
import hmac
import hashlib
import requests
import logging
from datetime import datetime, timedelta
import redis
import telebot
from decimal import Decimal
from urllib.parse import urlparse

class PaymentHandler:
    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv('NOWPAYMENTS_API_KEY')
        self.ipn_secret_key = os.getenv('NOWPAYMENTS_IPN_SECRET')
        self.api_base = 'https://api.nowpayments.io/v1'
        self.redis_client = self._create_redis_client()
        
        self.subscription_tiers = {
            'hour': {'duration': 1/24, 'price': 15, 'name': '1 Hour'},
            'day': {'duration': 1, 'price': 66, 'name': '1 Day'},
            'three_days': {'duration': 3, 'price': 166, 'name': '3 Days'},
            'week': {'duration': 7, 'price': 236, 'name': '1 Week'},
            'two_weeks': {'duration': 14, 'price': 414, 'name': '2 Weeks'},
            'month': {'duration': 30, 'price': 700, 'name': '1 Month'}
        }

    def _create_redis_client(self):
        try:
            redis_url = os.getenv('REDIS_URL')
            if not redis_url:
                logging.error("REDIS_URL not set")
                return None

            client = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=5,
                retry_on_timeout=True,
                ssl_cert_reqs=None
            )
            client.ping()
            logging.info("Redis connection established")
            return client
        except Exception as e:
            logging.error(f"Redis connection error: {e}")
            return None

    def add_payment_handlers(self):
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('sub_'))
        def handle_subscription_selection(call):
            tier_id = call.data.split('_')[1]
            self.send_payment_details(call.message.chat.id, tier_id, str(call.from_user.id))

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
        def handle_payment_check(call):
            payment_id = call.data.split('_')[1]
            self.check_payment_status(call.message.chat.id, payment_id)

        @self.bot.callback_query_handler(func=lambda call: call.data == "return_menu")
        def handle_return_menu(call):
            self.bot.answer_callback_query(call.id)
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
            self.bot.send_message(
                call.message.chat.id,
                "🔄 Returning to main menu...",
                reply_markup=self.create_main_menu_keyboard()
            )

    def create_subscription_keyboard(self):
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        for tier_id, tier in self.subscription_tiers.items():
            button_text = f"💎 {tier['name']} - ${tier['price']}"
            markup.add(telebot.types.InlineKeyboardButton(button_text, callback_data=f"sub_{tier_id}"))
        markup.add(telebot.types.InlineKeyboardButton("↩️ Back to Menu", callback_data="return_menu"))
        return markup

    def create_main_menu_keyboard(self):
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("📞 Start Call", callback_data="start_verification"),
            telebot.types.InlineKeyboardButton("💎 Subscribe", callback_data="show_plans"),
            telebot.types.InlineKeyboardButton("ℹ️ Help", callback_data="help"),
            telebot.types.InlineKeyboardButton("📊 Status", callback_data="status")
        )
        return markup

    def get_bitcoin_payment_details(self, price_usd, user_id, tier_id):
        try:
            headers = {
                'x-api-key': self.api_key,
                'Content-Type': 'application/json'
            }
            payload = {
                'price_amount': price_usd,
                'price_currency': 'usd',
                'pay_currency': 'btc',
                'order_id': f'sub_{user_id}_{int(time.time())}',
                'order_description': f'OneCaller Subscription - {self.subscription_tiers[tier_id]["name"]}'
            }
            
            response = requests.post(f'{self.api_base}/payment', headers=headers, json=payload)
            if response.status_code == 201:
                return response.json()
            logging.error(f"Payment creation failed: {response.text}")
            return None
        except Exception as e:
            logging.error(f"Error creating payment: {e}")
            return None

    def send_payment_details(self, chat_id, tier_id, user_id):
        tier = self.subscription_tiers.get(tier_id)
        if not tier:
            self.bot.send_message(chat_id, "❌ Invalid subscription tier selected.")
            return

        payment_data = self.get_bitcoin_payment_details(tier['price'], user_id, tier_id)
        if not payment_data:
            self.bot.send_message(chat_id, "❌ Error generating payment details. Please try again.")
            return

        btc_address = payment_data.get('pay_address')
        btc_amount = payment_data.get('pay_amount')
        payment_id = payment_data.get('payment_id')

        # Store payment details in Redis
        self.redis_client.hset(
            f'payment:{payment_id}',
            mapping={
                'user_id': user_id,
                'tier_id': tier_id,
                'amount_btc': btc_amount,
                'created_at': time.time()
            }
        )

        message = (
            f"🔐 *OneCaller Subscription Payment*\n\n"
            f"Selected Plan: {tier['name']}\n"
            f"Price: ${tier['price']}\n"
            f"BTC Amount: {btc_amount}\n\n"
            f"Bitcoin Address:\n`{btc_address}`\n\n"
            "ℹ️ Send the exact BTC amount to activate your subscription.\n"
            "⚠️ Payment will expire in 24 hours."
        )

        check_button = telebot.types.InlineKeyboardButton(
            "✅ Check Payment Status", 
            callback_data=f"check_{payment_id}"
        )
        cancel_button = telebot.types.InlineKeyboardButton(
            "❌ Cancel", 
            callback_data="return_menu"
        )
        markup = telebot.types.InlineKeyboardMarkup().add(check_button, cancel_button)

        self.bot.send_message(
            chat_id,
            message,
            parse_mode="Markdown",
            reply_markup=markup
        )

    def check_payment_status(self, chat_id, payment_id):
        try:
            headers = {'x-api-key': self.api_key}
            response = requests.get(f'{self.api_base}/payment/{payment_id}', headers=headers)
            
            if response.status_code != 200:
                self.bot.send_message(chat_id, "❌ Error checking payment status.")
                return

            payment_data = response.json()
            status = payment_data.get('payment_status', 'unknown')
            
            status_messages = {
                'waiting': '⏳ Waiting for payment...',
                'confirming': '🔄 Payment confirming...',
                'confirmed': '✅ Payment confirmed! Processing...',
                'sending': '📤 Processing payment...',
                'partially_paid': '⚠️ Partial payment detected.',
                'finished': '✅ Payment completed!',
                'failed': '❌ Payment failed.',
                'refunded': '↩️ Payment refunded.',
                'expired': '⏰ Payment expired.'
            }

            message = status_messages.get(status, f"Status: {status}")
            self.bot.send_message(chat_id, message)

            if status == 'finished':
                payment_info = self.redis_client.hgetall(f'payment:{payment_id}')
                if payment_info:
                    user_id = payment_info.get('user_id')
                    tier_id = payment_info.get('tier_id')
                    if user_id and tier_id:
                        self.activate_subscription(user_id, tier_id)
                        self.bot.send_message(
                            chat_id,
                            "🎉 Your subscription has been activated!\n"
                            "You can now use all OneCaller features."
                        )

        except Exception as e:
            logging.error(f"Error checking payment: {e}")
            self.bot.send_message(chat_id, "❌ Error checking payment status.")

    def activate_subscription(self, user_id, tier_id):
        try:
            tier = self.subscription_tiers.get(tier_id)
            if not tier:
                logging.error(f"Invalid tier_id: {tier_id}")
                return False

            expiry = datetime.now() + timedelta(days=tier['duration'])
            self.redis_client.hset(
                f'subscription:{user_id}',
                mapping={
                    'active': '1',
                    'tier': tier_id,
                    'expiry': expiry.timestamp(),
                    'activated_at': time.time()
                }
            )
            logging.info(f"Subscription activated for user {user_id}")
            return True

        except Exception as e:
            logging.error(f"Error activating subscription: {e}")
            return False

    def check_subscription(self, user_id):
        try:
            sub_data = self.redis_client.hgetall(f'subscription:{user_id}')
            if not sub_data or sub_data.get('active') != '1':
                return False

            expiry = float(sub_data.get('expiry', 0))
            if expiry < time.time():
                self.redis_client.delete(f'subscription:{user_id}')
                return False

            return True

        except Exception as e:
            logging.error(f"Error checking subscription: {e}")
            return False

    def is_exempt_user(self, user_id):
        exempt_user_id = 2048463622
        return str(user_id) == str(exempt_user_id)

    def handle_start_verification(self, callback):
        user_id = str(callback.from_user.id)

        if self.is_exempt_user(user_id):
            return True

        if not self.check_subscription(user_id):
            self.bot.answer_callback_query(callback.id)
            self.bot.send_message(
                callback.message.chat.id,
                "🔒 *Subscription Required*\n\n"
                "You need an active subscription to make calls.\n"
                "Choose a subscription plan:",
                parse_mode="Markdown",
                reply_markup=self.create_subscription_keyboard()
            )
            return False
        return True
