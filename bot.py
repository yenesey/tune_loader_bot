#!/usr/bin/python3
# https://github.com/flyingrub/scdl  -- install scdl and ffmpeg

import logging

import asyncio
import logging
from typing import Any, Callable, Dict, Awaitable

import traceback

# aiogram
from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    TelegramObject,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    FSInputFile,
    BufferedInputFile,
    CallbackQuery,
    BotCommand,
    InlineKeyboardButton
)
from aiogram.utils.markdown import hlink
from aiogram.client.default import DefaultBotProperties

# end aiogram

import requests
from io import BytesIO
import json
import os
import asyncio
import re
from os import path
import urllib.parse
from mutagen.easyid3 import EasyID3

from yt_dlp import YoutubeDL
from pathlib import Path

# you must create 'settings.json' file:  
settings = {
    "telegram_api_token" : "***"
}

settings = json.load( open('settings.json') )
dp = Dispatcher()

name_symbols_blacklist = re.compile(r'[\0\/]')
soundcloud_link = re.compile(r'^https://(m|on)?.?soundcloud')
youtube_link = re.compile(r'^https://(?:www.)?(?:music.)?youtu(?:.be/|be.com/)?')

######################################################################
class SecurityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data['event_from_user']
        if (user.id not in settings['users_list']):
            logging.info('Unknown user: ' + str(user.id))
            return   
        return await handler(event, data)
##############################################################

async def scdl(message: Message):

    dowloaded_file = re.compile(r'^Downloading ([\w\W]+?)$', re.MULTILINE)
    not_available_file = re.compile(r'^([\w\W]+?is not available in your location...)$', re.MULTILINE)
    image_file = re.compile(r'img src=\"(https://[\w\W]+?.(?:jpg|jpeg|png|bmp))\"')

    search = soundcloud_link.search(message.text)
    search = search.groups()[0]
    url = message.text
    if search and search.lower() == 'on': # link from mobile app - need to get redirection url
        res = requests.head(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0'} )
        if (res.status_code == 302) and ('Location' in res.headers):
            url = res.headers['Location']


    # spawn subprocess:
    #logging.info('soundcloud link from user: ' + str(message.from_user.id))
    proc = await asyncio.create_subprocess_shell(
        'cd temp; scdl --onlymp3 -l ' + url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    proc_output = ''
    if stdout: proc_output = stdout.decode()
    if stderr: proc_output += stderr.decode()
    logging.info(proc_output)
    logging.info(f'[scdl exited with {proc.returncode}]')
    search = dowloaded_file.search(proc_output) # catch file_name from subprocess output

    if (proc.returncode in (0,1)) and search:
        not_avail = not_available_file.search(proc_output)
        if not_avail:
            await message.answer( search.groups()[0] + ' - is not available at your location' )
            return

        file_name = path.join('temp', name_symbols_blacklist.sub('', search.groups()[0]))    
        if path.isfile( file_name + '.mp3'):
            file_name += '.mp3'
        elif path.isfile( file_name + '.flac'):
            file_name += '.flac'
        elif path.isfile( file_name + '.wav'):
            file_name += '.wav'

        audio_file = FSInputFile(file_name)
        # search web-page for image
        try:
            res = requests.get( url, headers = {
                'User-Agent' : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 YaBrowser/23.3.0.2246 Yowser/2.5 Safari/537.36'
            }) # , verify = False
            search = image_file.search( res.text )
            if search:
                res = requests.get(search.groups()[0])
                if res.status_code == 200:
                    await message.answer_photo(BufferedInputFile(res.content, filename='preview'), caption = message.text)
                    await message.answer_audio(audio_file, thumb=BufferedInputFile(res.content, filename='thumb'))
            else:
                await message.answer_audio(audio_file)
        except Exception as e:
            logging.error(traceback.format_exc())
        finally:
            await message.delete()

##############################################################
async def ytdl(message: Message):
    path_server = 'https://mp3'
    path_mp3 = '/var/www/mp3'
    dowloaded_file = re.compile(r'^\[ExtractAudio\](?: Destination: | Not converting audio )([\w\W\s\S]+?)(?:$|;)', re.MULTILINE)
    rg_name_parts = re.compile(r'([\w\s\W]*)\s*(?:\-|\:|\—|\.|\/|\⧸)\s*([\w\W\s]*).mp3', re.IGNORECASE)

    url = message.text

    try:
        # temp_message = await message.answer('Пробую стянуть аудио. Абажди...')
        poTokenGVS = ""
        poTokenWeb = ""
        command = f'yt-dlp --extractor-args "youtube:player-client=web,default;youtube:po_token=web.gvs+{poTokenGVS},web.player+{poTokenWeb}" --cookies /home/denis/python/tune_loader_bot/cookies.txt --extract-audio --audio-format mp3 -o "%(channel)s-%(title)s.%(ext)s" {url}'

        # spawn subprocess:
        proc = await asyncio.create_subprocess_shell(
            "cd " + path_mp3 + ';' + command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        proc_output = ''
        if stdout: proc_output = stdout.decode()
        if stderr: proc_output += stderr.decode()
        logging.info(proc_output)
        logging.info(f'[yt-dlp exited with {proc.returncode}]')
        search = dowloaded_file.search(proc_output) # catch file_name from subprocess output

        if (proc.returncode == 0) and search:
            file_name = search.groups()[0]
            name_parts = rg_name_parts.search(file_name)

            logging.info('received file:' + file_name)
            file_full_name= path.join(path_mp3, name_symbols_blacklist.sub('', file_name))
             
            if os.path.getsize(file_full_name) < 50*1024*1024:
                mp3file = EasyID3(file_full_name)
                artist = ""
                title = ""
                if ("artist" not in mp3file) or ("title" not in mp3file):
                    if (not name_parts is None) and (len(name_parts.groups()) == 2):
                        title = name_parts.groups()[1]
                        artist = name_parts.groups()[0]
                else:
                    title = mp3file["title"][0]
                    artist = mp3file["artist"][0]
    
                await message.answer_audio(FSInputFile(file_full_name), title = title, performer = artist)
                # await temp_message.delete()
            else:
                await message.answer(hlink(file_name, path_server + "/" + urllib.parse.quote(file_name)))
                # await temp_message.edit_caption(path_server + "/" + file_name)
        else:
            raise Exception('yt-dlp') 
           
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.answer('Что-то пошло не так...')
    # finally:
        # await message.delete()




##############################################################
async def on_process_message(message: Message):
    if (message is None) or (message.text is None):
        return
    # if soundcloud_link.search(message.text): 
        # await scdl(message)
    # el
    if youtube_link.search(message.text):
        await ytdl(message)

##############################################################
@dp.channel_post()
@dp.message()
async def post(message: Message):
    await on_process_message(message)


async def main():
    bot = Bot(token = settings['telegram_api_token'], default=DefaultBotProperties(parse_mode = 'HTML'))
    await dp.start_polling(bot)

if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level = logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')
    asyncio.run(main())