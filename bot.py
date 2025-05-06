#!/usr/bin/python3
import logging
import asyncio
import traceback
import json
import os
import urllib.parse
import re
from datetime import datetime
import sqlite3
from typing import Any, Callable, Dict, Awaitable

from aiogram import Bot, Dispatcher #, Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import (
    TelegramObject,
    Message,
    FSInputFile,
    InputMediaAudio,
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
    "telegramАpiToken" : "***",
    "usersList": [],
    "poTokenGVS": "***",
    "poTokenWeb": "***",
    "downloadFileDir": "/var/www/***",
    "serverRootUrl": "https://server/mp3",
}
'''
SETTINGS = json.load( open('settings.json') )

YTDL_OPTS = {
    "paths": {"temp" : SETTINGS["downloadFileDir"], "home": SETTINGS["downloadFileDir"]},
    "extractor_args": {
        "player_client" : "web",
        "youtube" : {"po_token" : [f"web.gvs+{SETTINGS['poTokenGVS']}" , f"web.player+{SETTINGS['poTokenWeb']}" ]}
    },
    "cookiefile" : os.path.join(os.getcwd(), "cookies.txt"),
    "postprocessors": [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "192",
    }],
    "format": "bestaudio/best",
    "outtmpl": "%(channel)s——%(artist)s——%(title)s",
    "progress_hooks": None,
    "postprocessor_hooks": None,
    "overwrites": True,
    # 'skip_download': True,
    # "verbose": True
}

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
            if (user.id not in SETTINGS['usersList']):
                logging.info('Unknown user: ' + str(user.id))
                return   
        return await handler(event, data)


##############################################################
async def download_yt_dlp(work_dir, url):
    global YTDL_OPTS

    result = None
    def postproc(d):
        nonlocal result
        if d['status'] == 'finished':
            base_name = os.path.basename(d['info_dict']['filename'])
            result = base_name + '.mp3'
            print('\n[postprocessor:finished] ' + base_name)

    def progress(d):
        nonlocal result
        if d['status'] == 'finished':
            base_name = os.path.basename(d['filename'])
            result = base_name + '.mp3'
            print('\n[progress:finished] ' + base_name)

    YTDL_OPTS["paths"]["home"] = work_dir
    YTDL_OPTS["progress_hooks"] = [progress]
    YTDL_OPTS["postprocessor_hooks"] = [postproc]
    try:
        with YoutubeDL(YTDL_OPTS) as ydl:
            await asyncio.to_thread(ydl.download, [url])
    except DownloadError as e:
        print(f"[error downloading:] {url}: {str(e)}")
    
    return result

# name_symbols_blacklist = re.compile(r'[\0\/]')
def artist_title(file_name) -> (str, str):
    # extract artist and title from file name, assume delimiter is '——'
    # example file_name: 'NA——Dusty Springfield——Dusty Springfield - Son Of A Preacher Man.mp3'
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

def get_dirs(on_date) -> (str, str):
    sub_dir = os.path.join(str(on_date.year), f'{str(on_date.year)}-{str(on_date.month).zfill(2)}')
    return (f'{SETTINGS["serverRootUrl"]}/{sub_dir}',
        os.path.join(SETTINGS["downloadFileDir"], sub_dir))

def check_dir_exists(target_dir):
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)

async def download(message: Message):
    global SETTINGS
    conn = None
    try:
        answer_message = await message.answer('Processing. Please wait for a while...')
        conn = sqlite3.connect("downloads.db")
        db_select = conn.execute('SELECT date, file_name FROM downloads WHERE url = ?', [message.text]).fetchone()

        if db_select is not None:
            on_date = datetime.strptime(db_select[0][:10], "%Y-%m-%d").date()
            server_dir, target_dir = get_dirs(on_date)
            target_file_name = db_select[1]
        else:
            on_date = datetime.now()
            server_dir, target_dir = get_dirs(on_date)
            check_dir_exists(target_dir)
            target_file_name = await download_yt_dlp(target_dir, message.text)

            user_id = ''
            if message.from_user is not None:
                user_id = str(message.from_user.id)

            conn.execute('INSERT INTO downloads(date, user_id, url, file_name) VALUES(?, ?, ? ,?) ', 
                [on_date, user_id, message.text, target_file_name]
            )
            conn.commit()
            conn.close()

        if target_file_name is not None:
            artist, title = artist_title(target_file_name)
            full_name = os.path.join(target_dir, target_file_name)
            server_url = f'{server_dir}/{urllib.parse.quote(target_file_name)}'   
            if os.path.getsize(full_name) < 50*1024*1024:
                # await message.answer_audio(FSInputFile(full_name), title = title, performer = artist, caption = hlink("#link", server_url))
                media = InputMediaAudio(media = FSInputFile(full_name), title = title, performer = artist, caption = hlink("#origin", message.text) + '  ' + hlink("#file", server_url))
                await answer_message.edit_media(media)
            else:
                # await message.answer(hlink(f"{artist} - {title}", server_url)) 
                await answer_message.edit_text(hlink(f"{artist} - {title}", server_url) + '\n' + hlink("#origin", message.text))
            
            await message.delete()
        else:
            raise Exception('download_yt_dlp')
           
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.answer('something went wrong...')
    finally:
        if not conn is None:
            conn.close()

##############################################################
link_types = {
    'youtube':  re.compile(r'^https://(?:www.)?(?:music.)?youtu(?:.be/|be.com/)?'),
    'soundcloud': re.compile(r'^https://(m|on)?.?soundcloud'),
    'yandex': re.compile(r'https?://music\.yandex\.(?P<tld>ru|kz|ua|by|com)'),
    'rutube': re.compile(r'https?://rutube\.ru/(?:(?:live/)?video(?:/private)?|(?:play/)?embed)/(?P<id>[\da-z]{32})'),
    # 'coub' :  re.compile(r'(?:coub:|https?://(?:coub\.com/(?:view|embed|coubs)/|c-cdn\.coub\.com/fb-player\.swf\?.*\bcoub(?:ID|id)=))(?P<id>[\da-z]+)')
}

dp = Dispatcher()

@dp.channel_post()
@dp.message()
async def on_process_message(message: Message):
    global soundcloud_link
    global youtube_link
    if (message is None) or (message.text is None):
        return
    for lnk in link_types:
        if link_types[lnk].search(message.text):
            await download(message)


##############################################################

async def main():
    bot = Bot(token = SETTINGS['telegramАpiToken'], default = DefaultBotProperties(parse_mode = 'HTML'))
    await dp.start_polling(bot)

if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level = logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')
    if not os.path.isfile('downloads.db'):
        conn = sqlite3.connect('downloads.db')
        conn.execute('CREATE TABLE IF NOT EXISTS downloads (date DATETIME, user_id STRING, url STRING PRIMARY KEY, file_name STRING)')
        # db.execute('CREATE INDEX IF NOT EXISTS date_index ON downloads (date)')
        conn.commit()
        conn.close()
    dp.update.outer_middleware( SecurityMiddleware() )
    asyncio.run(main())
 