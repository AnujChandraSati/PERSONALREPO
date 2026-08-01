#!/usr/bin/env python3
"""
Quick standalone test: can GitHub Actions fetch a Reddit post's plain HTML
page (not .json) and find the image URL inside it?

This does NOT hit .json at all - it fetches the normal web page a browser
would see, and looks for the og:image meta tag, which Reddit sets to the
post's actual image URL for link previews. This is a genuinely different
code path from the .json endpoint that got blocked.

Usage: set REDDIT_POST_URL env var, run this file directly.
Prints either "SUCCESS: <image_url>" or "FAILED: <http_status_or_error>".
"""

import os
import re
import sys
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def main():
    post_url = os.environ.get("REDDIT_POST_URL", "").strip()
    if not post_url:
        print("FAILED: no REDDIT_POST_URL set")
        sys.exit(1)

    print(f"Fetching plain HTML page: {post_url}")
    try:
        resp = requests.get(post_url, headers=HEADERS, timeout=15)
    except Exception as e:
        print(f"FAILED: request exception: {e}")
        sys.exit(1)

    print(f"HTTP status: {resp.status_code}")
    print(f"Response length: {len(resp.text)} chars")

    if resp.status_code != 200:
        print(f"FAILED: got HTTP {resp.status_code}, not 200")
        print("First 500 chars of response body:")
        print(resp.text[:500])
        sys.exit(1)

    match = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', resp.text)
    if not match:
        match = re.search(r'<meta\s+content="([^"]+)"\s+property="og:image"', resp.text)

    if match:
        image_url = match.group(1).replace("&amp;", "&")
        print(f"SUCCESS: {image_url}")
        sys.exit(0)
    else:
        print("FAILED: got 200 but could not find og:image meta tag")
        print("This might be a text/gallery/video post, or Reddit changed its HTML structure.")
        sys.exit(1)


if __name__ == "__main__":
    main()
