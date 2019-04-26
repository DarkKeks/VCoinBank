import logging
import os

from vk_api import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

logFormatter = logging.Formatter(
    '%(levelname)-5s [%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('VCoinGame')

console_handler = logging.StreamHandler()
console_handler.setFormatter(logFormatter)

logger.addHandler(console_handler)
logger.setLevel(logging.INFO)


group_id = os.environ.get('GROUP_ID')
group_token = os.environ.get('GROUP_TOKEN')


class Messages:

    Commands = """Вы можете использовать следующие команды: 

💰 Купить - купить VK Coin

📋 Цены - узнать текущий курс продажи VK Coin

📝 Инструкция — получить инструкцию по покупке VK Coin"""

    Intro = """✌️ Привет!
Этот бот позволяет купить VK Coin по выгодному курсу‼️
Если сомневаешься в нашей честности, можешь прочитать отзывы наших покупателей: https://vk.cc/9gFQYk

"""

    BuyGuide = """📝 Инструкция по покупке VK Coin у бота: vk.cc/9giWdY"""
    Buy = """💰 Оформление заказа и оплата: vk.cc/9gj1GK
    
Отзывы покупателей: https://vk.cc/9gFQYk    
    
После оплаты отправьте сюда уникальный код, выданный Вам после оплаты!"""

    Rate = """Текущий курс (бот только продает коины, не покупает): 
    
💰 100 000 VK Coin - 9 рублей, 
💰 500 000 VK Coin - 45 рублей, 
💰 1 000 000 VK Coin - 90 рублей, 
💰 2 000 000 VK Coin - 162 рублей (скидка 10%‼️)"""

    Bonus = """Инструкция о том, как получить VK Coin бесплатно: vk.cc/9giVX9"""


class Bot:
    def __init__(self):
        self.session = vk_api.VkApi(token=group_token)
        self.bot = VkBotLongPoll(self.session, group_id)
        self.api = self.session.get_api()

        self.main_keyboard = VkKeyboard(one_time=False)

        def add_button(text, color=VkKeyboardColor.DEFAULT, payload=''):
            self.main_keyboard.add_button(text, color=color, payload=payload)

        add_button('Купить', color=VkKeyboardColor.POSITIVE)
        add_button('Инструкция')
        self.main_keyboard.add_line()
        add_button('Цены', color=VkKeyboardColor.DEFAULT)
        self.main_keyboard.add_line()
        add_button('Получить VK Coins бесплатно!', color=VkKeyboardColor.NEGATIVE)

    def start(self):
        for event in self.bot.listen():
            if event.type == VkBotEventType.MESSAGE_NEW:
                id = event.object.from_id

                if self.has_market_attachment(event.object):
                    self.send_message(id, Messages.Buy)
                elif 'text' in event.object:
                    message = event.object.text.strip().lower()
                    if message == 'начать':
                        self.send_message(id, Messages.Intro + Messages.Commands)
                    elif message == 'купить':
                        self.send_message(id, Messages.Buy)
                    elif message == 'инструкция':
                        self.send_message(id, Messages.BuyGuide)
                    elif message == 'цены':
                        self.send_message(id, Messages.Rate)
                    elif message == 'Получить VK Coins бесплатно!'.lower():
                        self.send_message(id, Messages.Bonus)
                    else:
                        self.send_message(id, Messages.Commands)
                else:
                    self.send_message(id, Messages.Commands)

    def send_message(self, id, message):
        self.api.messages.send(
            peer_id=id,
            random_id=get_random_id(),
            keyboard=self.main_keyboard.get_keyboard(),
            message=message
        )

    @staticmethod
    def has_market_attachment(message):
        if 'attachments' in message:
            for item in message['attachments']:
                if item['type'] == 'market':
                    return True
        return False


if __name__ == '__main__':
    Bot().start()
