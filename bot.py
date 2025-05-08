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

from aiogram import Bot, Dispatcher #, Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import (
    TelegramObject,
    Message,
    FSInputFile,
    InputMediaAudio,
    InputMediaVideo,
    BotCommand,
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

YTDL_OPTS = {
    "paths": {"temp" : SETTINGS["download-dir"], "home": SETTINGS["download-dir"]},
    "extractor_args": {
        "player_client" : "web",
        "youtube" : {"po_token" : [f"web.gvs+{SETTINGS['po-token-gvs']}" , f"web.player+{SETTINGS['po-token-web']}" ]}
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
    "outtmpl": "%(channel)s——%(artist)s——%(title)s.%(ext)s",
    "progress_hooks": [],
    "postprocessor_hooks": [],
    "overwrites": True,
    # "verbose": True
}

class Database:
    _instance = None
    def __new__(self, *args, **kwargs):
        if self._instance is None:
            self._instance = super().__new__(self, *args, **kwargs)
        return self._instance
    
    @classmethod
    async def create(cls):
        # if not os.path.isfile('downloads.db'):
        conn = await aiosqlite.connect('downloads.db')
        await conn.execute('CREATE TABLE IF NOT EXISTS downloads (date DATETIME, user_id STRING, url STRING PRIMARY KEY, file_name STRING, file_size BIGINT)')
        await conn.commit()
        inst = cls()
        inst._conn = conn
        return inst

    async def find_url(self, url):
        cursor = await self._conn.execute('SELECT date, file_name, file_size FROM downloads WHERE url = ?', [url])
        fetch_data = await cursor.fetchone()
        return {
            "date" : datetime.strptime(fetch_data[0][:10], "%Y-%m-%d").date(),
            "file_name" : fetch_data[1],
            "file_size" : fetch_data[2]
        } if fetch_data else None

    async def save(self, on_date, user_id, url, file_name, file_size):
        await self._conn.execute('INSERT INTO downloads(date, user_id, url, file_name, file_size) VALUES(?, ?, ?, ?, ?)',
            [on_date, user_id, url, file_name, file_size]
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
        if 'event_from_user' in data:
            user = data['event_from_user']
            if (user.id not in SETTINGS['users-list']):
                logging.info('Unknown user: ' + str(user.id))
                return   
        return await handler(event, data)

##############################################################
async def download_yt_dlp(work_dir, url, video = False):
    result = None
    def postproc(d):
        nonlocal result
        if result:
            return

        if d["postprocessor"] == 'ExtractAudio':
            if d['status'] == 'finished':
                filename = os.path.basename(d['info_dict']['filename'])
                result = filename[:filename.rfind('.')] + '.mp3'
                logging.info('[ExtractAudio:finished] ' + result)

        if d["postprocessor"] == 'VideoConvertor':
            if d['status'] == 'finished':
                filename = os.path.basename(d['info_dict']['filename'])
                result = filename[:filename.rfind('.')] + '.mp4'
                logging.info('[VideoConvertor:finished] ' + result)

        if d["postprocessor"] == 'MoveFiles':
            if d['status'] == 'finished':
                result = os.path.basename(d['info_dict']['filename'])

    opts = copy.deepcopy(YTDL_OPTS)
    opts["logger"] = logging
    opts["paths"]["home"] = work_dir
    opts["postprocessor_hooks"] = [postproc]
    del opts["postprocessors"][int(not video)] # remove unwanted postprocessor

    try:
        with YoutubeDL(opts) as ydl:
            await asyncio.to_thread(ydl.download, [url])
    except DownloadError as e:
        print(f"[error downloading:] {url}: {str(e)}")
    
    return result

def artist_title(file_name) -> (str, str):
    '''
    extract artist and title from file name, assume delimiter is '——'
    example file_name: 'NA——Dusty Springfield——Dusty Springfield - Son Of A Preacher Man.mp3'
    '''
    name_parts = [name.strip() for name in file_name.split('——') if name != 'NA']
    title = ''
    artist = ''
    if len(name_parts) == 0:
       raise Exception(f"something wrong with name parts:{file_name}")
    elif len(name_parts) == 1:
        title = name_parts[0][:-4]
    else:
        artist,title = name_parts[-2:]
        title = title[:-4] # cut off ".mp3"
    return artist, title

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
    try:
        if key == 'youtube-video':
            url = message.text[1:]
            InputMedia = InputMediaVideo
        else:
            url = message.text
            InputMedia = InputMediaAudio

        instant_answer = await message.answer('Processing. Please wait for a while...')
        found = await DB.find_url(message.text)
        if found:
            on_date = found["date"]
            target_dir = get_download_dir(on_date)
            file_name = found["file_name"]
            file_name_path = os.path.join(target_dir, file_name)
            file_size = found["file_size"]
        else:
            on_date = datetime.now()
            target_dir = get_download_dir(on_date)
            ensure_directory_exists(target_dir)
            file_name = await download_yt_dlp(target_dir, url, key == 'youtube-video')
            file_name_path = os.path.join(target_dir, file_name)
            file_size = os.path.getsize(file_name_path)
            await DB.save(on_date, (str(message.from_user.id) if message.from_user else None), message.text, file_name, file_size)
 
        if file_name:
            artist, title = artist_title(file_name)
            server_url = get_server_url(on_date, file_name)
            if file_size < 50*1024*1024:
                await instant_answer.edit_media(
                    InputMedia(media = FSInputFile(file_name_path), title = title, performer = artist,
                        caption = hlink("#origin", url) + '  ' + hlink("#file", server_url))
                )
            else:
                await instant_answer.edit_text(hlink(f"{artist} - {title}", server_url) + '\n' + hlink("#origin", message.text))
            await message.delete()
      
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.answer('Something went wrong...')


##############################################################
link_types = {
    'youtube':  re.compile(r'^https://(?:www.)?(?:music.)?youtu(?:.be/|be.com/)?'),
    'youtube-video':  re.compile(r'^Vhttps://(?:www.)?(?:music.)?youtu(?:.be/|be.com/)?'),
    'soundcloud': re.compile(r'^https://(m|on)?.?soundcloud'),
    'yandex': re.compile(r'https?://music\.yandex\.(?P<tld>ru|kz|ua|by|com)'),
    'rutube': re.compile(r'https?://rutube\.ru/(?:(?:live/)?video(?:/private)?|(?:play/)?embed)/(?P<id>[\da-z]{32})'),
    # 'coub' :  re.compile(r'(?:coub:|https?://(?:coub\.com/(?:view|embed|coubs)/|c-cdn\.coub\.com/fb-player\.swf\?.*\bcoub(?:ID|id)=))(?P<id>[\da-z]+)')
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
    bot = Bot(token = SETTINGS['telegram-api-token'], default = DefaultBotProperties(parse_mode = 'HTML'))
    await dp.start_polling(bot)

if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level = logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')
    dp.update.outer_middleware( SecurityMiddleware() )
    asyncio.run(main())
 