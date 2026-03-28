import logging
import asyncio
# import copy
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
import os
import json

from settings import SETTINGS

YTDL_OPTS = {
    "paths": {"temp" : SETTINGS["download-dir"], "home": SETTINGS["download-dir"]},
    "extractor_args": {
        "player_client" : "web",
        "youtube" : {
            "po_token" : [f'web.gvs+{SETTINGS["po-token-gvs"]}', f'web.player+{SETTINGS["po-token-web"]}' ],
            "youtube_player_js_version": "actual",
            "youtube_player_js_variant": "main",
        },
        "youtubetab" : {
            "skip" : "authcheck"
        }
    },
    "js_runtimes" : {"deno": {"path": "/home/denis/.deno/bin/deno"}},
    "cookiefile" : os.path.join(os.getcwd(), "cookies.txt"),
    "postprocessors": [],
    # "postprocessor_args": {
        # "videoconvertor": ["-c:v", "libx264", "-preset",  "fast", "-crf", "23", "-c:a", "aac", "-b:a" "128k"]
    # },
    # "format": "bestvideo*+bestaudio/best",
    # "format": "best",
    # "format": "(mp4)[height<960]",
    "outtmpl": f"%(title).100s",
    "outtmpl_na_placeholder": "",
    "progress_hooks": [],
    "postprocessor_hooks": [],
    "overwrites": True,
    "logger": logging,
    "extract_flat": "in_playlist",
    # "verbose": True
}

async def download_media(target_dir, url, video = False) -> dict:
    result = None
    postproc_status = {}

    def postproc(d):
        nonlocal result
        nonlocal postproc_status
        info = d["info_dict"]
        pp = d["postprocessor"]
        status = d["status"]
        if not (pp in postproc_status and status == postproc_status[pp]):
            logging.info(pp + ":" + status  + ":" + info.get("title")  )
        postproc_status[pp] = status
        if not result:
            if (pp == "MoveFiles") and (status == "finished"):
                filename = os.path.basename(info.get("filepath"))
                filepath = os.path.join(target_dir, filename)
                result = {
                    "title" : info.get("title"),
                    "artist" : info.get("artist"),
                    "channel" : info.get("channel") or info.get("uploader"),
                    "file_name" : filename,
                    "file_path" : filepath,
                    "file_size": os.path.getsize(filepath),
                }
                # logging.info(json.dumps(result, indent=2, ensure_ascii=False))
                logging.info(result)

    YTDL_OPTS["paths"]["home"] = target_dir
    YTDL_OPTS["postprocessor_hooks"] = [postproc]
    YTDL_OPTS["postprocessors"] = [{
            "key": "FFmpegVideoConvertor", 
            "preferedformat": "mp4"
        }] if video else [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    YTDL_OPTS["format"] = "(mp4,webm)[height<960]" if video else "best"

    try:
        with YoutubeDL(YTDL_OPTS) as ydl:
            logging.info(f"Schedule download: {url}")
            await asyncio.to_thread(ydl.download, [url])

    except DownloadError as e:
        logging.error(f"Error downloading: {url}: {str(e)}")
    return result


async def download_playlist(url, items = "1"):
    YTDL_OPTS["playlist_items"] = items
    with YoutubeDL(YTDL_OPTS) as ydl:
        # note: logging with "logger" works only on DEBUG level
        info = await asyncio.to_thread(ydl.extract_info, url = url, download = False)
        return info["entries"]