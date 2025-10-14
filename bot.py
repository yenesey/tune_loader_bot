#!/usr/bin/python3
import logging
import asyncio
import traceback
import os
import urllib.parse
import re
from datetime import datetime

from typing import Any, Callable, Dict, Awaitable

from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Bot, Dispatcher #, Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import (
    TelegramObject,
    Message,
    FSInputFile,
    InputMediaAudio,
    InputMediaVideo,
    BotCommand,
    CallbackQuery,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.markdown import hlink
from aiogram.client.default import DefaultBotProperties
######################################################################
from download import download
from settings import SETTINGS
from database import Database
######################################################################
USER_DATA = {}
DB = None

class TuneLoaderStates(StatesGroup):
    select_action = State()


######################################################################
class SecurityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if "event_from_user" in data:
            user = data["event_from_user"]
            if (user.id not in SETTINGS["users-list"]):
                logging.info(f'Unknown user: {str(user.id)}')
                return   
        return await handler(event, data)

##############################################################
def sub_dir(on_date: datetime):
    return os.path.join(str(on_date.year), f'{str(on_date.year)}-{str(on_date.month).zfill(2)}')

def get_server_url(on_date: datetime, file_name : str) -> str:
    return f'{SETTINGS["server-root-url"]}/{sub_dir(on_date)}/{urllib.parse.quote(file_name)}'
       
def get_download_dir(on_date: datetime) -> str:
    return f'{SETTINGS["download-dir"]}/{sub_dir(on_date)}'      
       
def ensure_directory_exists(target_dir):
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)

async def process_message(message: Message, video: bool):
    url = message.text
    InputMedia = InputMediaAudio
    if video:
        InputMedia = InputMediaVideo

    logging.info("Received URL: " + url)

    try:
        instant_answer = await message.answer("Processing. Please wait for a while...")
        file_name_path = ""
        found = await DB.find_url(url, video)
        if found:
            on_date = found["date"]
            target_dir = get_download_dir(on_date)
            file_name_path = os.path.join(target_dir, found["file_name"])
        else:
            on_date = datetime.now()
            target_dir = get_download_dir(on_date)
            ensure_directory_exists(target_dir)
            loaded = await download(target_dir, url, video)

            if loaded:
                file_name_path = os.path.join(target_dir, loaded["file_name"])
                user_id = str(message.from_user.id) if message.from_user else None
                await DB.save(on_date, user_id, url, video, loaded["file_name"], loaded["file_size"], loaded["title"])
    
        result = found or loaded

        if result:
            title = result["title"]
            split = title.split(SETTINGS["name-sep"])
            artist = split[len(split)-2]
            title2 = split[len(split)-1]

            server_url = get_server_url(on_date, result["file_name"])
            if result["file_size"] < 50*1024*1024:
                await instant_answer.edit_media(
                    InputMedia(media = FSInputFile(file_name_path), title = title2, performer = artist,
                        caption = hlink("#origin", url) + "  " + hlink("#file", server_url))
                )
            else:
                await instant_answer.edit_text(hlink(f"{title}", server_url) + "\n" + hlink("#origin", url))
            await message.delete()
        else:
            await message.answer("Something went wrong...")
      
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.answer("Something went wrong...")


##############################################################
def check_url(url):
    supported_urls = {
        "youtube": re.compile(r'^https://(?:www.)?(?:music.)?youtu(?:.be/|be.com/)?'),
        "soundcloud": re.compile(r'^https://(m|on)?.?soundcloud'),
        "yandex": re.compile(r'^https?://music\.yandex\.(?P<tld>ru|kz|ua|by|com)'),
        "rutube": re.compile(r'^https?://rutube\.ru/(?:(?:live/)?video(?:/private)?|(?:play/)?embed)/(?P<id>[\da-z]{32})'),
        "coub" :  re.compile(r'^(?:coub:|https?://(?:coub\.com/(?:view|embed|coubs)/|c-cdn\.coub\.com/fb-player\.swf\?.*\bcoub(?:ID|id)=))(?P<id>[\da-z]+)'),
        "tiktok": re.compile(r'^https://(?:www.)?(?:vt.)?tiktok.com/'),
    }
    for key in supported_urls:
        if supported_urls[key].search(url):
            return key
    return None


def create_download_dialog(key) -> dict:
    options = {
        "youtube": ("audio", "video"),
        "soundcloud": ("audio"),
        "yandex": ("audio"), 
        "rutube": ("audio"),
        "coub" : ("audio", "video"),
        "tiktok": ("video"),
    }

    buttons = {
        "audio": ('🎶Audio', 'audio'),
        "video": ('🎞Video', 'video'),
    }

    kbd = InlineKeyboardBuilder()
    text_and_data = [ buttons[btn] for btn in buttons if btn in options[key] ] + [('❌', 'exit')]
    row_btns = (InlineKeyboardButton(text = text, callback_data = data) for text, data in text_and_data)
    kbd.row(*row_btns)
    return {"text": "Может качнем эту ☝ ссылку?", "reply_markup": kbd.as_markup() }


dp = Dispatcher()

@dp.callback_query(StateFilter(TuneLoaderStates.select_action))
async def inline_kb_answer_callback_handler(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await query.bot.delete_message(chat_id = query.message.chat.id, message_id = query.message.message_id)
    if query.data != 'exit':
        await process_message(USER_DATA[query.from_user.id], query.data == 'video')
    await state.clear()
 
@dp.message()
async def on_process_message(message: Message, state: FSMContext):
    if message and message.text:
        url_type = check_url(message.text)
        if url_type:
            USER_DATA[message.from_user.id] = message
            await message.answer(**create_download_dialog(url_type))
            await state.set_state(TuneLoaderStates.select_action)

@dp.channel_post()
async def on_process_channel_post(message: Message):
    if message and message.text:
        if check_url(message.text):
            await process_message(message, False)

##############################################################
async def main():
    global DB
    DB = await Database.create()
    bot = Bot(token = SETTINGS["telegram-api-token"], default = DefaultBotProperties(parse_mode = "HTML"))
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)-8s %(message)s", level = logging.INFO, datefmt="%Y-%m-%d %H:%M:%S")
    dp.update.outer_middleware( SecurityMiddleware() )
    asyncio.run(main())
 