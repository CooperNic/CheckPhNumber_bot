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
    await message.answer("Hello you typed START command!")

@dp.message()
async def cmd_start(message: types.Message):
    phone = message.text
    phone = phone.replace("-", "").replace("(", "").replace(")", "").replace(" ", "").replace("+", "")
    print(f"phone={phone}")
    cursor.execute('SELECT * FROM numbers WHERE fullnumber=' + phone)
    number = cursor.fetchall()
    print(number)
    await message.reply(number[0][6]+" ("+number[0][7]+")")

#
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

con.close()
