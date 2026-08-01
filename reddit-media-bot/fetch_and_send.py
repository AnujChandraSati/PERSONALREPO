#!/usr/bin/env python3
"""
Runs inside the GitHub Action. Given a Reddit post URL (from n8n's
repository_dispatch payload), downloads the media with RedDownloader,
uploads it directly to the Telegram group, then deletes the original
RSS link message so the media replaces it.
"""

import os
import glob
import json
import mimetypes
import requests
from RedDownloader import RedDownloader

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
POST_URL = os.environ["POST_URL"]
MESSAGE_ID = os.environ.get("MESSAGE_ID") or None

API = f"https://api.telegram.org/bot{TOKEN}"
WORKDIR = "/tmp/dl"
OUTPUT_NAME = "media"
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".gif"}

os.makedirs(WORKDIR, exist_ok=True)


def tg_post(method, data=None, files=None):
    resp = requests.post(f"{API}/{method}", data=data, files=files, timeout=60)
    if not resp.ok:
        print(f"Telegram {method} failed: {resp.status_code} {resp.text}")
    return resp


def send_message(text):
    tg_post("sendMessage", {"chat_id": CHAT_ID, "text": text})


def send_photo(path):
    with open(path, "rb") as f:
        tg_post("sendPhoto", {"chat_id": CHAT_ID}, {"photo": f})


def send_video(path):
    with open(path, "rb") as f:
        tg_post("sendVideo", {"chat_id": CHAT_ID}, {"video": f})


def send_media_group(paths):
    media, files, opened = [], {}, []
    for i, p in enumerate(paths[:10]):  # Telegram's per-group cap
        name = f"file{i}"
        guess = mimetypes.guess_type(p)[0] or ""
        mtype = "video" if "video" in guess else "photo"
        media.append({"type": mtype, "media": f"attach://{name}"})
        f = open(p, "rb")
        opened.append(f)
        files[name] = (os.path.basename(p), f)
    tg_post("sendMediaGroup", {"chat_id": CHAT_ID, "media": json.dumps(media)}, files)
    for f in opened:
        f.close()


def delete_original():
    if MESSAGE_ID:
        tg_post("deleteMessage", {"chat_id": CHAT_ID, "message_id": MESSAGE_ID})


def find_downloaded_files():
    # RedDownloader writes <output>.<ext> for a single file, or a folder
    # named <output>/ for galleries, inside `destination`.
    folder = os.path.join(WORKDIR, OUTPUT_NAME)
    if os.path.isdir(folder):
        return sorted(f for f in glob.glob(os.path.join(folder, "*")) if os.path.isfile(f))
    return sorted(glob.glob(os.path.join(WORKDIR, f"{OUTPUT_NAME}.*")))


def main():
    try:
        RedDownloader.Download(POST_URL, destination=WORKDIR, output=OUTPUT_NAME, verbose=False)
    except Exception as e:
        send_message(f"Couldn't download this post, leaving the link up:\n{POST_URL}\n\n{e}")
        return

    files = find_downloaded_files()
    if not files:
        send_message(f"No media found for this post, leaving the link up:\n{POST_URL}")
        return

    if len(files) == 1:
        is_video = os.path.splitext(files[0])[1].lower() in VIDEO_EXTS
        (send_video if is_video else send_photo)(files[0])
    else:
        send_media_group(files)

    delete_original()


if __name__ == "__main__":
    main()
