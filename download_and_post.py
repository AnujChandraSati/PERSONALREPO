#!/usr/bin/env python3
"""
Reddit photo downloader -> Telegram poster.

Given a Reddit post URL, this script:
  1. Fetches the post's JSON data from Reddit (no auth needed for public posts).
  2. Figures out whether it's a single image post or a gallery post.
  3. Downloads the image(s).
  4. Posts them straight to a Telegram chat using the Bot API.

Exit code 0  = success (at least one image posted).
Exit code 1+ = failure (nothing posted) -> lets the calling GitHub Actions
               job report failure, so n8n's fallback chain moves to the
               next repo.

Required environment variables (set as GitHub Actions secrets / inputs):
  REDDIT_POST_URL     - the Reddit post URL, e.g. https://www.reddit.com/r/horror/comments/abc123/some_title/
  TELEGRAM_BOT_TOKEN  - the shared bot token (GitHub Actions secret)
  TELEGRAM_CHAT_ID    - target chat/channel id for this run (passed in per-dispatch by n8n)
"""

import os
import sys
import time
import json
import re
import requests

REDDIT_HEADERS = {
    # Reddit blocks requests with no / generic User-Agent. Be a good citizen and identify clearly.
    "User-Agent": "python:reddit-story-media-fetcher:v1.0 (personal single-user script)"
}

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 3


def log(msg):
    print(f"[downloader] {msg}", flush=True)


def normalize_reddit_url(url: str) -> str:
    """Ensure we hit the .json API endpoint for the post, stripping query params/trailing slash quirks."""
    url = url.strip()
    url = url.split("?")[0]
    if not url.endswith("/"):
        url += "/"
    if not url.endswith(".json"):
        url = url.rstrip("/") + ".json"
    return url


def fetch_post_json(post_url: str) -> dict:
    api_url = normalize_reddit_url(post_url)
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(api_url, headers=REDDIT_HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_err = str(e)
        log(f"Attempt {attempt}/{MAX_RETRIES} fetching post JSON failed: {last_err}")
        time.sleep(RETRY_BACKOFF_SECONDS)
    raise RuntimeError(f"Could not fetch Reddit post JSON after {MAX_RETRIES} attempts: {last_err}")


def extract_image_urls(post_json) -> list[str]:
    """
    Handles two cases:
      - Single image post (post_hint == 'image', or a plain i.redd.it link)
      - Gallery post (is_gallery True, media_metadata holds per-image info)
    Returns a list of direct, downloadable image URLs.
    """
    try:
        post_data = post_json[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Reddit JSON shape: {e}")

    urls = []

    # Gallery post
    if post_data.get("is_gallery") and post_data.get("media_metadata"):
        gallery_items = post_data.get("gallery_data", {}).get("items", [])
        media_metadata = post_data["media_metadata"]
        # Preserve gallery order using gallery_data.items, fall back to metadata dict order.
        ordered_ids = [item["media_id"] for item in gallery_items] if gallery_items else list(media_metadata.keys())
        for media_id in ordered_ids:
            meta = media_metadata.get(media_id)
            if not meta:
                continue
            # meta["s"] holds the "source" (largest) rendition. Fall back to largest preview if missing.
            source = meta.get("s", {})
            img_url = source.get("u") or source.get("gif")
            if not img_url and meta.get("p"):
                img_url = meta["p"][-1].get("u")  # largest preview as fallback
            if img_url:
                urls.append(img_url.replace("&amp;", "&"))
        if urls:
            return urls

    # Single image post (i.redd.it direct link, or url_overridden_by_dest pointing at an image)
    direct_url = post_data.get("url_overridden_by_dest") or post_data.get("url")
    if direct_url and re.search(r"\.(jpg|jpeg|png|webp|gif)($|\?)", direct_url, re.IGNORECASE):
        return [direct_url]

    # Some single-image posts store it under preview.images[0].source.url instead
    preview = post_data.get("preview", {}).get("images", [])
    if preview:
        source_url = preview[0].get("source", {}).get("url")
        if source_url:
            return [source_url.replace("&amp;", "&")]

    return urls  # possibly empty -> caller treats as failure


def download_image(url: str, dest_path: str) -> bool:
    try:
        resp = requests.get(url, headers=REDDIT_HEADERS, timeout=30, stream=True)
        if resp.status_code != 200:
            log(f"Failed to download {url}: HTTP {resp.status_code}")
            return False
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        log(f"Exception downloading {url}: {e}")
        return False


def send_photo_to_telegram(bot_token: str, chat_id: str, photo_path: str, caption: str = "") -> bool:
    url = TELEGRAM_API.format(token=bot_token, method="sendPhoto")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(photo_path, "rb") as f:
                resp = requests.post(
                    url,
                    data={"chat_id": chat_id, "caption": caption[:1024]},
                    files={"photo": f},
                    timeout=30,
                )
            if resp.status_code == 200 and resp.json().get("ok"):
                return True
            log(f"Attempt {attempt}/{MAX_RETRIES} Telegram send failed: {resp.status_code} {resp.text[:300]}")
        except Exception as e:
            log(f"Attempt {attempt}/{MAX_RETRIES} Telegram send exception: {e}")
        time.sleep(RETRY_BACKOFF_SECONDS)
    return False


def main():
    post_url = os.environ.get("REDDIT_POST_URL", "").strip()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not post_url or not bot_token or not chat_id:
        log("Missing one of REDDIT_POST_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        sys.exit(1)

    log(f"Processing post: {post_url}")

    try:
        post_json = fetch_post_json(post_url)
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)

    try:
        image_urls = extract_image_urls(post_json)
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)

    if not image_urls:
        log("No image URLs found in this post (it may be a text/video/link post). Nothing to send.")
        sys.exit(1)

    log(f"Found {len(image_urls)} image(s) to send.")

    os.makedirs("downloads", exist_ok=True)
    sent_count = 0
    for idx, img_url in enumerate(image_urls, start=1):
        ext = "jpg"
        m = re.search(r"\.(jpg|jpeg|png|webp|gif)($|\?)", img_url, re.IGNORECASE)
        if m:
            ext = m.group(1).lower()
        dest_path = f"downloads/image_{idx}.{ext}"

        if not download_image(img_url, dest_path):
            log(f"Skipping image {idx}: download failed.")
            continue

        caption = post_url if idx == 1 else ""
        if send_photo_to_telegram(bot_token, chat_id, dest_path, caption=caption):
            sent_count += 1
            log(f"Sent image {idx}/{len(image_urls)} to Telegram.")
        else:
            log(f"Failed to send image {idx} to Telegram.")

    if sent_count == 0:
        log("FATAL: Downloaded images but failed to send any to Telegram.")
        sys.exit(1)

    log(f"Done. Successfully sent {sent_count}/{len(image_urls)} image(s).")
    sys.exit(0)


if __name__ == "__main__":
    main()
