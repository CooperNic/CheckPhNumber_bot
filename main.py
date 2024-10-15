import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
import sqlite3 as sl
from dotenv import load_dotenv
import time
import logging


load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")

# Создаём объект логгера
logger = logging.getLogger(__name__)

# Устанавливаем уровень логирования
logger.setLevel(logging.INFO)

# Создаем обработчик для записи в файл
file_handler = logging.FileHandler('access.log')

# Форматируем вывод
formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
file_handler.setFormatter(formatter)

# Добавляем обработчик к логгеру
logger.addHandler(file_handler)

# открываем файл с базой данных
con = sl.connect('db.sqlite/numeration_registry.db')
cursor = con.cursor()

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        """
        Реестр российской системы и плана нумерации. Для получения информации о принадлежности 
        номера оператору и региону введите номер телефона в формате 7<код><номер>
        """
        )

@dp.message()
async def cmd_start(message: types.Message):
    entered_phone_number = message.text
    phone = entered_phone_number.replace("-", "").replace("(", "").replace(")", "").replace(" ", "").replace("+", "")
    print(f"Введенный номер={entered_phone_number}, нормализованный номер={phone}, full_name, username, id = '{message.from_user.full_name}', '@{message.from_user.username}', '{message.from_user.id}'")
    start_time = time.time()
    cursor.execute('SELECT * FROM numbers WHERE fullnumber=' + phone)
    number = cursor.fetchall()
    end_time = time.time()
    execution_time = end_time - start_time
    print(f"Время выполнения: {execution_time} секунд. ")
    log_txt_end = "|"+ message.from_user.full_name if message.from_user.full_name is not None  else "None" 
    log_txt_end = log_txt_end + "|" + "@" + message.from_user.username if message.from_user.username is not None  else "None"
    log_txt_end = log_txt_end + "|" + str(message.from_user.id) if message.from_user.id is not None  else "None"
    log_txt_end = log_txt_end + "|" + str(execution_time) if execution_time is not None  else "None"
    if number:
        print(number)
        logger.info(entered_phone_number + "|"+ str(number[0][2]) + "|"+ number[0][6] + "|" + number[0][7] + log_txt_end )
        await message.reply(number[0][6]+" ("+number[0][7]+")")
    else:
        print("Номер не найден.")
        logger.info(entered_phone_number + "|Номер не найден||" + log_txt_end )
        await message.reply("Номер не найден.")
#
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

con.close()
