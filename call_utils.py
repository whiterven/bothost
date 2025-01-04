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

    def handle_bank_selection(self, call, bot, user_states):
        chat_id = call.message.chat.id
        
        if call.data == "custom_bank":
            bot.answer_callback_query(call.id)
            if isinstance(user_states.get(chat_id), dict):
                user_states[chat_id]["state"] = "awaiting_custom_bank"
            else:
                user_states[chat_id] = {"state": "awaiting_custom_bank"}
            
            bot.send_message(
                chat_id,
                "📝 Please enter the name of the bank:"
            )
            return

        if call.data.startswith("bank_"):
            bank_name = call.data[5:]
            bot.answer_callback_query(call.id, text=f"Selected: {bank_name}")
            if isinstance(user_states.get(chat_id), dict):
                user_states[chat_id]["state"] = "awaiting_phone"
                user_states[chat_id]["bank"] = bank_name
            else:
                user_states[chat_id] = {"state": "awaiting_phone", "bank": bank_name}
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
        user_state = user_states.get(chat_id, {}).get("state")
        
        if user_state == "awaiting_custom_bank":
            custom_bank = message.text.strip()
            user_states[chat_id] = {
                "state": "awaiting_phone",
                "bank": custom_bank
            }
            bot.send_message(
                chat_id,
                f"Selected bank: {custom_bank}\n\n"
                "📱 Enter the phone number to verify:\n"
                "Format: +[country_code][number]\n"
                "Example: +15017122661"
            )
            return True
        return False
