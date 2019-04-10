import hashlib
import json
import os
import re
import psycopg2
import requests

from datetime import datetime

from vk_api import vk_api
from vk_api.utils import get_random_id
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

from requests import RequestException
from apscheduler.schedulers.background import BackgroundScheduler

merchant_url = 'https://www.digiseller.market/asp2/pay_wm.asp?id_d=2629111&lang=ru-RU'

group_id = os.environ.get('GROUP_ID')
group_token = os.environ.get('GROUP_TOKEN')
bot_token = os.environ.get('BOT_TOKEN')
merchant_password = os.environ.get('MERCHANT_PASSWORD')

bot_base_endpoint = f'http://y1.codepaste.ml:8080/{bot_token}'
bot_status_endpoint = f'{bot_base_endpoint}/status'
bot_transfer_endpoint = f'{bot_base_endpoint}/transfer?to={{to}}&amount={{amount}}'


class Messages:

    Commands = """Список команд, которые можно использовать: 

Покупка - получить инструкции по покупке VK Coin

Курс - узнать текущий курс продажи VK Coin

Бонусы - узнать список бонусов, которые предоставляет площадка VCoinBank"""

    Intro = """Привет!
Этот бот позволяет купить VK Coin по выгодному курсу!
"""

    BuyGuide = """Инструкция по покупке VK Coin у бота: vk.cc/9giWdY"""
    Buy = """Оформление заказа и оплата: vk.cc/9gj1GK
    
Отзывы покупателей: https://vk.cc/9gFQYk    
    
После оплаты отправьте сюда уникальный код, выданный Вам после оплаты!"""

    Rate = """Текущий курс (бот только продает коины, не покупает): 
    
100 000 VK Coin - 4 рубля, 
500 000 VK Coin - 18 рублей, 
2 000 000 VK Coin - 64 рубля, 
5 000 000 VK Coin - 160 рублей."""

    NotAvailable = """Пока что бот недоступен, но это скоро исправится! Попробуй немного позже!"""

    Available = """Бот доступен, баланс бота - {}"""

    Bonus = """Инструкция о том, как получить VK Coin бесплатно: vk.cc/9giVX9"""

    UsedAlready = """Код уже был использован."""

    InvalidCode = """Неверный код."""

    TransferSuccess = """{count} коинов успешно перечислено! Спасибо за покупку

Отзыв о покупке можно оставить здесь: vk.cc/9gFQYk"""

    TransferFailed = """Перевод не удался, попробуйте позже :("""


class BotManager:
    def __init__(self):
        self.status = {'status': 'error', 'message': 'not requested yet'}

    @staticmethod
    def transfer(vk_id, amount):
        try:
            return requests.get(bot_transfer_endpoint.format(
                to=vk_id,
                amount=int(amount * 1e6)
            )).json()
        except RequestException:
            return {
                'success': False
            }

    def update_status(self):
        try:
            response = requests.get(bot_status_endpoint, timeout=2)
        except RequestException:
            response = None

        if response and response.status_code == requests.codes.ok:
            self.status = {
                'status': 'success',
                'data': response.json(),
                'last_available': datetime.now()
            }
        else:
            last_available = bot_manager.status.get('last_available')
            self.status = {
                'status': 'error',
                'message': response.status_code if response else -1,
            }
            if last_available:
                self.status['last_available'] = last_available


class CodeManager:
    CHECK_URL = 'http://shop.digiseller.ru/xml/check_unique_code.asp'
    SELLER_ID = 382663
    PASSWORD = merchant_password

    def __init__(self):
        self.connection = psycopg2.connect(
            os.environ.get('DATABASE_URL')
        )

    def check_not_used(self, code):
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM used_codes WHERE code = %s", (code,))
        result = cursor.fetchone()[0] == 0
        cursor.close()
        return result

    def set_used(self, id, code):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO used_codes (code, user_id) VALUES (%s, %s)", (code, id))
        self.connection.commit()
        cursor.close()

    def delete_used(self, code):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM used_codes WHERE code = %s", (code,))
        self.connection.commit()

    @staticmethod
    def check_merchant(code):
        sign = hashlib.md5()
        string = f"{CodeManager.SELLER_ID}:{code}:{CodeManager.PASSWORD}"
        sign.update(string.encode('utf-8'))

        response = requests.post(CodeManager.CHECK_URL, json={
            'id_seller': CodeManager.SELLER_ID,
            'unique_code': code,
            'sign': sign.hexdigest()
        }).json()

        if int(response['retval']) == 0:
            return {
                'valid': True,
                'count': float(response['cnt_goods'].replace(',', '.')),
                'reponse': response
            }
        else:
            return {
                'valid': False
            }

    def save(self, info, code):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO merchant_info (data, code) SELECT %s, %s "
                       "WHERE NOT EXISTS ( "
                       "    SELECT id FROM merchant_info WHERE code = %s"
                       ")", (json.dumps(info), code, code))
        self.connection.commit()
        cursor.close()


class Bot:
    def __init__(self, code_manager, bot_manager):
        self.bot_manager = bot_manager
        self.code_manager = code_manager
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
                        if self.is_code(message):
                            self.send_message(id, 'Обрабатываем код')
                            self.process_code(id, message)
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

    @staticmethod
    def is_code(message):
        return re.match(r'[a-zA-Z0-9]{16}', message) is not None

    def process_code(self, id, code):
        if self.code_manager.check_not_used(code):
            purchase_info = self.code_manager.check_merchant(code)
            if purchase_info['valid']:
                self.code_manager.save(purchase_info, code)
                self.code_manager.set_used(id, code)

                result = self.bot_manager.transfer(id, purchase_info['count'])
                if result['success']:
                    formatted_count = self.format_coin_count(purchase_info['count'])
                    self.send_message(id, Messages.TransferSuccess.format(count=formatted_count))
                else:
                    self.code_manager.delete_used(code)
                    self.send_message(id, Messages.TransferFailed)
            else:
                self.send_message(id, Messages.InvalidCode)
        else:
            self.send_message(id, Messages.UsedAlready)

    @staticmethod
    def format_coin_count(count):
        return int(count * 1e6) / 1e3


bot_manager = BotManager()
code_manager = CodeManager()
bot = Bot(code_manager, bot_manager)

scheduler = BackgroundScheduler(daemon=True)
scheduler.start()


@scheduler.scheduled_job(trigger='interval', seconds=5)
def update_status():
    bot_manager.update_status()


if __name__ == '__main__':
    bot.start()
