import hashlib
import json
import os
import re
from datetime import datetime

import psycopg2
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from requests import RequestException
from vk_api import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

merchant_url = 'https://www.digiseller.market/asp2/pay_wm.asp?id_d=2629111&lang=ru-RU'

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

Бонусы - узнать список бонусов, которые предоставляет площадка VCoinBank

Статус - узнать статус бота, перед покупкой VK Coin следует проверить, доступен ли бот"""

    Intro = """Привет!
Этот бот позволяет купить VK Coin по выгодному курсу!
"""

    BuyGuide = """Инструкция по покупке VK Coin у бота: vk.cc/9giWdY"""

    Rate = """Текущий курс (бот только продает коины, не покупает): 
    
1000 VK Coin - 0.2 рубля, 
10 000 VK Coin - 2 рубля, 
100 000 VK Coin - 20 рублей, 
1 000 000 VK Coin - 200 рублей"""

    NotAvailable = """Пока что бот недоступен, но это скоро исправится! Попробуй немного позже!"""

    Available = """Бот доступен, баланс бота - {}"""

    Bonus = """Инструкция о том, как получить VK Coin бесплатно: vk.cc/9giVX9"""

    UsedAlready = """Код уже был использован."""

    InvalidCode = """Неверный код."""

    TransferSuccess = """Коины успешно перечислены! Спасибо за покупку"""

    TransferFailed = """Перевод не удался, попробуйте позже :("""


class BotManager:

    def __init__(self):
        self.status = {'status': 'error', 'message': 'not requested yet'}

    def transfer(self, id, amount):
        try:
            return requests.get(bot_transfer_endpoint.format(
                to=id,
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

        if response is not None and response.status_code == requests.codes.ok:
            self.status = {
                'status': 'success',
                'data': response.json(),
                'last_available': datetime.now()
            }
        else:
            last_available = bot_manager.status.get('last_available')
            self.status = {
                'status': 'error',
                'message': response.status_code if response is not None else -1,
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

    def check_merchant(self, code):
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
        self.bot = VkBotLongPoll(self.session, 180860084)
        self.api = self.session.get_api()

        self.main_keyboard = VkKeyboard(one_time=False)

        self.main_keyboard.add_button('Покупка', color=VkKeyboardColor.POSITIVE)
        self.main_keyboard.add_line()
        self.main_keyboard.add_button('Курс', color=VkKeyboardColor.DEFAULT)
        self.main_keyboard.add_button('Статус', color=VkKeyboardColor.DEFAULT)
        self.main_keyboard.add_line()
        self.main_keyboard.add_button('Бонусы', color=VkKeyboardColor.PRIMARY)

    def start(self):
        for event in self.bot.listen():
            if event.type == VkBotEventType.MESSAGE_NEW:
                id = event.object.from_id
                if 'text' in event.object:
                    message = event.object.text.strip()
                    if message == 'Начать':
                        self.send_message(id, Messages.Intro + Messages.Commands)
                    elif message == 'Покупка':
                        self.send_message(id, Messages.BuyGuide)
                    elif message == 'Курс':
                        self.send_message(id, Messages.Rate)
                    elif message == 'Бонусы':
                        self.send_message(id, Messages.Bonus)
                    elif message == 'Статус':
                        if bot_manager.status['status'] == 'error':
                            self.send_message(id, Messages.NotAvailable)
                        else:
                            status = bot_manager.status
                            self.send_message(id, Messages.Available.format(status['data']['score'] / 1e3))
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

    def is_code(self, message):
        return re.match(r'[a-zA-Z0-9]{16}', message) is not None

    def process_code(self, id, code):
        if self.code_manager.check_not_used(code):
            purchase_info = self.code_manager.check_merchant(code)
            if purchase_info['valid']:
                self.code_manager.save(purchase_info, code)
                self.code_manager.set_used(id, code)

                result = self.bot_manager.transfer(id, purchase_info['count'])
                if result['success']:
                    self.send_message(id, Messages.TransferSuccess)
                else:
                    self.code_manager.delete_used(code)
                    self.send_message(id, Messages.TransferFailed)
            else:
                self.send_message(id, Messages.InvalidCode)
        else:
            self.send_message(id, Messages.UsedAlready)


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
