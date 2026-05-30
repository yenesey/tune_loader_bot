#!/usr/bin/python3
import asyncio
import logging
import os
import re
import traceback
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict

from aiogram import Bot, Dispatcher  # , Router
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InputMediaAudio,
    InputMediaVideo,
    Message,
    TelegramObject,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hlink

from database import Database

######################################################################
from download import download_media, download_playlist

# from watchdog import watchdog
from settings import SETTINGS

######################################################################
USER_DATA = {}
TG_MAX_FILE_SIZE = 50 * 1024 * 1024
DB = Database()


######################################################################
class DownloadActions(StatesGroup):
    select_action = State()


class WatchdogActions(StatesGroup):
    select_action = State()


######################################################################
class SecurityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if "event_from_user" in data:
            user = data["event_from_user"]
            if user.id not in SETTINGS["users-list"]:
                logging.info(f"Unknown user: {str(user.id)}")
                logging.info(f"message.text: {data.keys()}")
                return
        return await handler(event, data)


##############################################################
def sub_dir(on_date: datetime):
    return os.path.join(
        str(on_date.year), f"{str(on_date.year)}-{str(on_date.month).zfill(2)}"
    )


def enrich_info(info):
    if not info or not isinstance(info, dict):
        return None
    info["performer"] = info["artist"] or info["channel"]
    info["link_text"] = (
        info["performer"] + " - " + info["title"]
        if info["performer"]
        else info["title"]
    )
    info["server_url"] = (
        f"{SETTINGS['server-root-url']}/{sub_dir(info['date'])}/{urllib.parse.quote(info['file_name'])}"
    )
    if "file_path" not in info:
        info["file_path"] = os.path.join(
            f"{SETTINGS['download-dir']}/{sub_dir(info['date'])}", info["file_name"]
        )
    return info


async def find(url: str, video: bool) -> dict | None:
    found = await DB.find_url(url, video)
    if found:
        return enrich_info(found)


async def download(url: str, video: bool, user_id: str | None) -> dict:
    date = datetime.now()
    target_dir = f"{SETTINGS['download-dir']}/{sub_dir(date)}"
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)
    loaded = await download_media(target_dir, url, video)
    if loaded:
        loaded["date"] = date
        loaded["user_id"] = user_id
        loaded["url"] = url
        loaded["video"] = int(video)
        loaded["delivered"] = int(False)
        await DB.save_download(loaded)
        enrich_info(loaded)
    return loaded


async def process_message(message: Message, video: bool):
    if not message.text:
        return
    url = message.text or ""
    InputMedia = InputMediaVideo if video else InputMediaAudio
    try:
        instant_answer = await message.answer("Processing. Please wait for a while...")
        user_id = str(message.from_user.id) if message.from_user else None
        result = await find(url, video)
        if not result:
            result = await download(url, video, user_id)
        if result:
            if result["file_size"] < TG_MAX_FILE_SIZE:
                await instant_answer.edit_media(
                    InputMedia(
                        media=FSInputFile(result["file_path"]),
                        title=result["title"],
                        performer=result["performer"],
                        caption=hlink("#origin", url)
                        + "  "
                        + hlink("#file", result["server_url"]),
                    )
                )
            else:
                await instant_answer.edit_text(
                    hlink(result["link_text"], result["server_url"])
                    + "\n"
                    + hlink("#origin", url)
                )
            await message.delete()
        else:
            await message.answer("Something went wrong...")

    except Exception:
        logging.error(traceback.format_exc())
        await message.answer("Something went wrong...")


##############################################################
def check_url(url):
    supported_urls = {
        "youtube": re.compile(r"^https://(?:www.)?(?:music.)?youtu(?:.be/|be.com/)?"),
        "soundcloud": re.compile(r"^https://(m|on)?.?soundcloud"),
        "yandex": re.compile(r"^https?://music\.yandex\.(?P<tld>ru|kz|ua|by|com)"),
        "rutube": re.compile(
            r"^https?://rutube\.ru/(?:(?:live/)?video(?:/private)?|(?:play/)?embed)/(?P<id>[\da-z]{32})"
        ),
        "coub": re.compile(
            r"^(?:coub:|https?://(?:coub\.com/(?:view|embed|coubs)/|c-cdn\.coub\.com/fb-player\.swf\?.*\bcoub(?:ID|id)=))(?P<id>[\da-z]+)"
        ),
        "tiktok": re.compile(r"^https://(?:www.)?(?:vt.)?tiktok.com/"),
        "instagram": re.compile(
            r"(?P<url>https?://(?:www\.)?instagram\.com(?:/(?!share/)[^/?#]+)?/(?:p|tv|reels?(?!/audio/))/(?P<id>[^/?#&]+))"
        ),
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
        "rutube": ("audio", "video"),
        "coub": ("audio", "video"),
        "tiktok": ("video"),
        "instagram": ("video"),
    }

    buttons = {
        "audio": ("🎶Audio", "audio"),
        "video": ("📺Video", "video"),
    }

    kbd = InlineKeyboardBuilder()
    text_and_data = [buttons[btn] for btn in buttons if btn in options[key]] + [
        ("❌", "exit")
    ]
    row_btns = (
        InlineKeyboardButton(text=text, callback_data=data)
        for text, data in text_and_data
    )
    kbd.row(*row_btns)
    return {"text": "Может качнем эту ☝ ссылку?", "reply_markup": kbd.as_markup()}


dp = Dispatcher()


@dp.callback_query(StateFilter(DownloadActions.select_action))
async def inline_kb_answer_callback_handler(query: CallbackQuery, state: FSMContext):
    await query.answer()
    if query.bot and query.message:
        await query.bot.delete_message(
            chat_id=query.message.chat.id, message_id=query.message.message_id
        )
    if query.data != "exit":
        await process_message(USER_DATA[query.from_user.id], query.data == "video")
    await state.clear()


@dp.message()
async def on_process_message(message: Message, state: FSMContext):
    if message and message.text:
        url_type = check_url(message.text)
        if url_type:
            if message.from_user:
                USER_DATA[message.from_user.id] = message
            await message.answer(**create_download_dialog(url_type))
            await state.set_state(DownloadActions.select_action)


@dp.channel_post()
async def on_process_channel_post(message: Message):
    if message and message.text:
        if check_url(message.text):
            await process_message(message, False)


##############################################################


async def send_audio_message(bot: Bot, download):
    if download["file_size"] < TG_MAX_FILE_SIZE:
        await bot.send_audio(
            download["user_id"],
            FSInputFile(download["file_path"]),
            title=download["title"],
            performer=download["performer"],
            caption=hlink("#origin", download["url"])
            + "  "
            + hlink("#file", download["server_url"]),
        )
    else:
        await bot.send_message(
            download["user_id"],
            hlink(download["link_text"], download["server_url"])
            + "\n"
            + hlink("#origin", download["url"]),
        )
    download["delivered"] = int(True)
    await DB.save_download(download)


def scan_playlist(sub, playlist):
    urls = []
    for ent in playlist:
        if sub["keywords"]:
            for kwd in sub["keywords"].split(","):
                if kwd in ent["title"]:
                    urls.append(ent["url"])
        else:
            urls.append(ent["url"])
    urls.reverse()  # elders first
    return urls


async def watchdog(bot: Bot, DB: Database):
    while True:
        await asyncio.sleep(60)
        try:
            subscriptions = await DB.get_subscriptions()
            for sub in subscriptions:
                if (
                    sub["last_check"] is None
                    or sub["last_check"] + timedelta(seconds=sub["interval"])
                    < datetime.now()
                ):
                    sub["last_check"] = datetime.now()
                    await DB.save_subscription(sub)

                    playlist = await download_playlist(sub["url"], "1,2")
                    for url in scan_playlist(sub, playlist):
                        result = await DB.find_url(url, False, sub["user_id"])
                        if result:
                            if not result["delivered"]:
                                logging.info(
                                    f"download {url} not delivered: trying resend"
                                )
                                enrich_info(result)
                                result["url"] = url
                                result["user_id"] = sub["user_id"]
                                result["video"] = int(False)
                                await send_audio_message(bot, result)
                        else:
                            result = await download(url, False, sub["user_id"])
                            if result:
                                await send_audio_message(bot, result)
        except Exception:
            logging.error(traceback.format_exc())


async def main():
    global DB
    DB = await Database.create()
    bot = Bot(
        token=SETTINGS["telegram-api-token"],
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    _watchdog = asyncio.create_task(watchdog(bot, DB))
    _polling = dp.start_polling(bot)
    await asyncio.gather(_watchdog, _polling)


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    dp.update.outer_middleware(SecurityMiddleware())
    asyncio.run(main())
