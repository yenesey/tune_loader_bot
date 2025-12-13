import logging
import asyncio
import copy
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
import os

from settings import SETTINGS

YTDL_OPTS = {
    "paths": {"temp" : SETTINGS["download-dir"], "home": SETTINGS["download-dir"]},
    "extractor_args": {
        "player_client" : "web",
        "youtube" : {
            "po_token" : [f'web.gvs+{SETTINGS["po-token-gvs"]}', f'web.player+{SETTINGS["po-token-web"]}' ],
            "youtube_player_js_version": "actual",
            "youtube_player_js_variant": "main",
        }
    },
    "js_runtimes" : {"deno": {"path": "/home/denis/.deno/bin/deno"}},
    "cookiefile" : os.path.join(os.getcwd(), "cookies.txt"),
    "postprocessors": [],
    # "postprocessor_args": {
        # "videoconvertor": ["-c:v", "libx264", "-preset",  "fast", "-crf", "23", "-c:a", "aac", "-b:a" "128k"]
    # },
    "format": "bestvideo*+bestaudio/best",
    "outtmpl": f"%(title).100s",
    "outtmpl_na_placeholder": "",
    "progress_hooks": [],
    "postprocessor_hooks": [],
    "overwrites": True,
    # "verbose": True
}

async def download(target_dir, url, video = False) -> dict:
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
                title = SETTINGS["name-sep"].join([info[key] for key in ("channel", "artist", "title") if key in info])
                result = {
                    "title" : title,
                    "file_name" : filename,
                    "file_size": os.path.getsize(os.path.join(target_dir, filename))
                }
                logging.info(result)

    opts = copy.deepcopy(YTDL_OPTS)
    opts["logger"] = logging
    opts["paths"]["home"] = target_dir
    opts["postprocessor_hooks"] = [postproc]
    opts["postprocessors"].append({
            "key": "FFmpegVideoConvertor", 
            "preferedformat": "mp4"
        } if video else {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }
    )

    try:
        with YoutubeDL(opts) as ydl:
            logging.info(f'Schedule download: {url}')
            await asyncio.to_thread(ydl.download, [url])

    except DownloadError as e:
        logging.ERROR(f'Error downloading: {url}: {str(e)}')
    return result