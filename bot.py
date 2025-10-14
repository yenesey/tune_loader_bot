#!/usr/bin/python3
import logging
import asyncio
import aiosqlite
import traceback
import json
import os
import urllib.parse
import re
from datetime import datetime
import copy
from typing import Any, Callable, Dict, Awaitable

from aiogram.fsm.state import State, StatesGroup
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
    InlineKeyboardButton,
)
from aiogram.utils.markdown import hlink
from aiogram.client.default import DefaultBotProperties
######################################################################
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
######################################################################
'''
Example SETTINGS:
{
    "telegram-api-token" : "***",
    "po-token-gvs": "***",
    "po-token-web": "***",
    "download-dir": "/var/www/***",
    "server-root-url": "https://server/mp3",
    "users-list": [],
}
'''
SETTINGS = json.load( open('settings.json') )
NAME_SEP = "—"
# NAME_SEP = "-"

YTDL_OPTS = {
    "paths": {"temp" : SETTINGS["download-dir"], "home": SETTINGS["download-dir"]},
    "extractor_args": {
        "player_client" : "web",
        "youtube" : {"po_token" : [f'web.gvs+{SETTINGS["po-token-gvs"]}', f'web.player+{SETTINGS["po-token-web"]}' ]}
    },
    "cookiefile" : os.path.join(os.getcwd(), "cookies.txt"),
    "postprocessors": [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "192",
    },
    {
        "key": "FFmpegVideoConvertor", 
        "preferedformat": "mp4"
    }],
    # "postprocessor_args": {
        # "videoconvertor": ["-c:v", "libx264", "-preset",  "fast", "-crf", "23", "-c:a", "aac", "-b:a" "128k"]
    # },
    "format": "bestvideo*+bestaudio/best",
    # "outtmpl": f"%(channel)s{NAME_SEP}%(artist)s{NAME_SEP}%(title).100s",
    "outtmpl": f"%(title).100s",
    "outtmpl_na_placeholder": "",
    "progress_hooks": [],
    "postprocessor_hooks": [],
    "overwrites": True,
    # "verbose": True
}

class TuneLoaderStates(StatesGroup):
    select_action = State()
    execute_action = State()


class Database:
    _instance = None
    def __new__(self, *args, **kwargs):
        if self._instance is None:
            self._instance = super().__new__(self, *args, **kwargs)
        return self._instance
    
    @classmethod
    async def create(cls):
        # if not os.path.isfile("downloads.db"):
        conn = await aiosqlite.connect("downloads.db")
        await conn.execute("CREATE TABLE IF NOT EXISTS downloads (date DATETIME, user_id STRING, url STRING, video BOOLEAN, file_name STRING, file_size BIGINT, title STRING)")
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS url_and_type ON downloads (url, video)")
        await conn.commit()
        inst = cls()
        inst._conn = conn
        return inst

    async def find_url(self, url, video = False):
        cursor = await self._conn.execute("SELECT date, file_name, file_size, title FROM downloads WHERE url = ? and video = ?", [url, int(video)])
        fetch_data = await cursor.fetchone()
        return {
            "date" : datetime.strptime(fetch_data[0][:10], "%Y-%m-%d").date(),
            "file_name" : fetch_data[1],
            "file_size" : fetch_data[2],
            "title" : fetch_data[3],
        } if fetch_data else None

    async def save(self, on_date, user_id, url, video, file_name, file_size, title):
        await self._conn.execute("INSERT INTO downloads(date, user_id, url, video, file_name, file_size, title) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [on_date, user_id, url, video, file_name, file_size, title]
        )
        await self._conn.commit()

DB = None

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
async def download_yt_dlp(work_dir, url, video = False) -> dict:
    result = None

    def postproc(d):
        nonlocal result
        file_types = {
            "ExtractAudio" : ".mp3",
            "VideoConvertor": ".mp4",
        }
        if not result:
            pp = d["postprocessor"]
            if pp in ("ExtractAudio", "VideoConvertor"):
                if d["status"] == "finished":
                    # logging.info(d["info_dict"])
                    filename = os.path.basename(d["info_dict"]["filename"])
                    title = NAME_SEP.join([d["info_dict"][key] for key in ("channel", "artist", "title") if key in d["info_dict"]])
                    result = {
                        "file_ext" : file_types[pp],
                        "file_name" : filename,
                        "title" : title,
                    }
                    logging.info(f'{pp}: {filename}')

    opts = copy.deepcopy(YTDL_OPTS)
    opts["logger"] = logging
    opts["paths"]["home"] = work_dir
    opts["postprocessor_hooks"] = [postproc]
    del opts["postprocessors"][int(not video)] # remove unwanted postprocessor from copy

    try:
        with YoutubeDL(opts) as ydl:
            logging.info(f'Schedule download: {url}')
            await asyncio.to_thread(ydl.download, [url])
            if result:
                if not os.path.exists(os.path.join(work_dir, result["file_name"])):
                    if os.path.exists(os.path.join(work_dir, result["file_name"] + result["file_ext"])):
                        result["file_name"] = result["file_name"] + result["file_ext"]           
                    else:
                        logging.ERROR("something wrong with file name")
                result["file_size"] = os.path.getsize(os.path.join(work_dir, result["file_name"]))

    except DownloadError as e:
        logging.ERROR(f'Error downloading: {url}: {str(e)}')
    return result


def sub_dir(on_date: datetime):
    return os.path.join(str(on_date.year), f'{str(on_date.year)}-{str(on_date.month).zfill(2)}')

def get_server_url(on_date: datetime, file_name : str) -> str:
    return f'{SETTINGS["server-root-url"]}/{sub_dir(on_date)}/{urllib.parse.quote(file_name)}'
       
def get_download_dir(on_date: datetime) -> str:
    return f'{SETTINGS["download-dir"]}/{sub_dir(on_date)}'      
       
def ensure_directory_exists(target_dir):
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)

async def download(message: Message, key: str):
    video = False
    if key == "youtube-video":
        video = True
        url = message.text[1:]
        InputMedia = InputMediaVideo
    elif key == "coub":
        video = True
        url = message.text
        InputMedia = InputMediaVideo  
    elif key == 'tiktok':
        video = True
        url = message.text
        InputMedia = InputMediaVideo  
    else:
        url = message.text
        InputMedia = InputMediaAudio
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
            download = await download_yt_dlp(target_dir, url, video)

            if download:
                file_name_path = os.path.join(target_dir, download["file_name"])
                user_id = str(message.from_user.id) if message.from_user else None
                await DB.save(on_date, user_id, url, video, download["file_name"], download["file_size"], download["title"])
    
        result = found or download

        if result:
            title = result["title"]
            split = title.split(NAME_SEP)
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
link_types = {
    "youtube":  re.compile(r'^https://(?:www.)?(?:music.)?youtu(?:.be/|be.com/)?'),
    "youtube-video":  re.compile(r'^[V|v|!|#|В|в]https://(?:www.)?(?:music.)?youtu(?:.be/|be.com/)?'),
    "soundcloud": re.compile(r'^https://(m|on)?.?soundcloud'),
    "yandex": re.compile(r'^https?://music\.yandex\.(?P<tld>ru|kz|ua|by|com)'),
    "rutube": re.compile(r'^https?://rutube\.ru/(?:(?:live/)?video(?:/private)?|(?:play/)?embed)/(?P<id>[\da-z]{32})'),
    "coub" :  re.compile(r'^(?:coub:|https?://(?:coub\.com/(?:view|embed|coubs)/|c-cdn\.coub\.com/fb-player\.swf\?.*\bcoub(?:ID|id)=))(?P<id>[\da-z]+)'),
    "tiktok": re.compile(r'^https://(?:www.)?(?:vt.)?tiktok.com/'),
}

dp = Dispatcher()


@dp.channel_post()
@dp.message()
async def on_process_message(message: Message):
    if message and message.text:
        for key in link_types:
            if link_types[key].search(message.text):
                await download(message, key)

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
 