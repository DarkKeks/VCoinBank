import hashlib
import json
import logging
import os
import re
from enum import Enum

import psycopg2
import requests
from requests import RequestException
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

merchant_url = 'https://www.digiseller.market/asp2/pay_wm.asp?id_d=2629111&lang=ru-RU&referrer=bank&vk_id={id}'

group_id = os.environ.get('GROUP_ID')
group_token = os.environ.get('GROUP_TOKEN')
bot_token = os.environ.get('BOT_TOKEN')
merchant_password = os.environ.get('MERCHANT_PASSWORD')

merchant_id = os.environ.get('MERCHANT_ID')
merchant_key = os.environ.get('MERCHANT_KEY')


class Messages:
    Commands = """Вы можете использовать следующие команды: 

💰 Купить - купить VK Coin

📋 Цены - узнать текущий курс продажи VK Coin

📝 Инструкция — получить инструкцию по покупке VK Coin"""

    Intro = """✌️ Привет!
Этот бот позволяет купить VK Coin по выгодному курсу‼️
Если сомневаешься в нашей честности, можешь прочитать отзывы наших покупателей: https://vk.cc/9gFQYk

"""

    BuyGuide = """📝 Инструкция по покупке VK Coin у бота: https://vk.cc/9giWdY"""
    Buy = """💰 Оформление заказа и оплата: {url}
    
Отзывы покупателей: https://vk.cc/9gFQYk    
    
После оплаты отправьте сюда уникальный код, выданный Вам после оплаты!"""

    Rate = """Текущий курс (бот только продает коины, не покупает): 
    
💰 100 000 VK Coin - 9 рублей, 
💰 500 000 VK Coin - 45 рублей, 
💰 1 000 000 VK Coin - 90 рублей, 
💰 2 000 000 VK Coin - 162 рублей (скидка 10%‼️)"""

    NotAvailable = """😭 Пока что бот недоступен, но это скоро исправится! Попробуй немного позже! 😭"""

    Available = """Бот доступен, баланс бота - {}"""

    UsedAlready = """Код уже был использован."""

    InvalidCode = """Неверный код."""

    TransferSuccess = """{count} коинов успешно перечислено! Спасибо за покупку

✅ Отзыв о покупке можно оставить здесь: vk.cc/9gFQYk"""

    TransferFailed = """😭 Перевод не удался, попробуйте позже :(
Напишите сообщение "#проблема" (без кавычек), чтобы мы смогли помочь Вам."""


class CoinAPI:
    class Method(Enum):
        GET_TRANSACTIONS = 'tx'
        SEND = 'send'

    api_url = 'https://coin-without-bugs.vkforms.ru/merchant/{}/'
    headers = {'Content-Type': 'application/json'}

    def __init__(self, merchant_id, key):
        self.merchant_id = merchant_id
        self.key = key

        self.params = {
            'merchantId': self.merchant_id,
            'key': self.key
        }

    def send(self, to_id, amount):
        method_url = CoinAPI.api_url.format(CoinAPI.Method.SEND.value)

        params = self.params.copy()
        params.update({'toId': to_id})
        params.update({'amount': amount})

        return CoinAPI._send_request(method_url, json.dumps(params))

    @staticmethod
    def _send_request(url, params):
        try:
            response = requests.post(url, headers=CoinAPI.headers, data=params)

            if response.status_code == 200:
                return response.json().get('response')

            raise RequestException()
        except RequestException as e:
            logger.error(e)
        return None


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
        cursor.execute("SELECT transfered FROM used_codes WHERE code = %s", (code,))
        row = cursor.fetchone()
        result = row is None or row[0] is False
        cursor.close()
        return result

    def set_used(self, id, code):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO used_codes (code, user_id, referrer) VALUES (%s, %s, 'bank')"
                       "ON CONFLICT (code) DO UPDATE SET "
                       "user_id=excluded.user_id, "
                       "referrer=excluded.referrer, "
                       "time=now()", (code, id))
        self.connection.commit()
        cursor.close()

    def mark_success(self, code):
        cursor = self.connection.cursor()
        cursor.execute("UPDATE used_codes SET transfered = True WHERE code = %s", (code,))
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
    def __init__(self, code_manager, coin_api):
        self.coin_api = coin_api
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
                        self.send_message(id, Messages.Buy.format(url=self.get_url(id)))
                    elif message == 'инструкция':
                        self.send_message(id, Messages.BuyGuide)
                    elif message == 'цены':
                        self.send_message(id, Messages.Rate)
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
    def get_url(id):
        return merchant_url.format(id=id)

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

                result = self.coin_api.send(id, int(purchase_info['count'] * 1e6))

                if not result:
                    self.send_message(id, Messages.TransferFailed)
                else:
                    self.code_manager.mark_success(code)
                    formatted_count = self.format_coin_count(purchase_info['count'])
                    self.send_message(id, Messages.TransferSuccess.format(count=formatted_count))

            else:
                self.send_message(id, Messages.InvalidCode)
        else:
            self.send_message(id, Messages.UsedAlready)

    @staticmethod
    def format_coin_count(count):
        return int(count * 1e6) / 1e3


if __name__ == '__main__':
    coin_api = CoinAPI(merchant_id, merchant_key)
    code_manager = CodeManager()
    bot = Bot(code_manager, coin_api)

    bot.start()
