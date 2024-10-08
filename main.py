import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
import sqlite3 as sl
from dotenv import load_dotenv

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")

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
    cursor.execute('SELECT * FROM numbers WHERE fullnumber=' + phone)
    number = cursor.fetchall()
    if number:
        print(number)
        await message.reply(number[0][6]+" ("+number[0][7]+")")
    else:
        print("Номер не найден.")
        await message.reply("Номер не найден.")
#
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

con.close()
