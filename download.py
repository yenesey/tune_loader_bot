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
    "outtmpl": f"%(title).100s",
    "outtmpl_na_placeholder": "",
    "progress_hooks": [],
    "postprocessor_hooks": [],
    "overwrites": True,
    # "verbose": True
}

async def download_url(work_dir, url, video = False) -> dict:
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
                    title = SETTINGS["name-sep"].join([d["info_dict"][key] for key in ("channel", "artist", "title") if key in d["info_dict"]])
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