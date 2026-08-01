# Reddit Media Bot (n8n trigger → GitHub Actions → Telegram)

New RSS item lands in your Telegram group → a dedicated bot's n8n
workflow catches it → fires a GitHub Actions run → the Action downloads
the post's media with RedDownloader and sends it straight back to the
group with its own bot token → the original link message gets deleted.

n8n never touches Reddit or the media itself — its only job is to relay
the URL to GitHub. All downloading and sending happens inside the Action.

## 1. Create the dedicated bot

- @BotFather → `/newbot` → save the token.
- **It's a channel, so this bot must be added as an Administrator**,
  not just a member — bots only receive `channel_post` updates (the
  event this whole flow depends on) if they're an admin. Give it
  **Post Messages** and **Delete Messages** rights when you add it.
- Get the channel's chat ID (forward any post from the channel to
  [@userinfobot](https://t.me/userinfobot), or check n8n's Telegram
  Trigger output after step 3 below — channel chat IDs are negative
  numbers like `-1001234567890`).

## 2. Set up this repo

- Push this folder to a new GitHub repo.
- Repo secrets (Settings → Secrets and variables → Actions):
  | Secret | Value |
  |---|---|
  | `TELEGRAM_BOT_TOKEN` | the token from step 1 |
  | `TELEGRAM_CHAT_ID` | the group's chat ID from step 1 |
- Create a GitHub Personal Access Token for n8n to use (this is
  separate from the repo secrets above — n8n needs it to *call* the
  GitHub API, not to read it as a secret):
  - Fine-grained token → scope it to this repo → permission
    **Contents: Read and write** (this covers triggering
    `repository_dispatch`).
  - Copy the token now; GitHub only shows it once.

## 3. Build the n8n workflow (3 nodes)

**Node 1 — Telegram Trigger**
- Updates: `channel_post` only (not `message` — it's a channel, and
  you don't want `edited_channel_post` re-firing this on edits).
- Credential: the bot token from step 1.

**Node 2 — Code node, parse the update**
```javascript
const text = $json.channel_post?.text || $json.channel_post?.caption || '';
const match = text.match(/https?:\/\/(?:www\.|old\.)?reddit\.com\/r\/\w+\/comments\/\w+[^\s]*|https?:\/\/redd\.it\/\w+/i);
if (!match) return []; // not a reddit link — stop this branch

return [{
  json: {
    url: match[0],
    message_id: $json.channel_post.message_id
  }
}];
```

**Node 3 — HTTP Request, fire the GitHub Action**
- Method: `POST`
- URL: `https://api.github.com/repos/<owner>/<repo>/dispatches`
- Headers:
  - `Authorization: Bearer <the PAT from step 2>`
  - `Accept: application/vnd.github+json`
- Body (JSON):
  ```json
  {
    "event_type": "new-reddit-post",
    "client_payload": {
      "url": "={{ $json.url }}",
      "message_id": "={{ $json.message_id }}"
    }
  }
  ```

That's the whole n8n side — it just relays two fields and is done in
under a second. GitHub takes it from there.

## How the Action decides what to send

`RedDownloader.Download()` writes either `media.<ext>` (single image,
video, or gif) or a `media/` folder (gallery/carousel) into a temp
directory. The script checks which one exists, then:
- one image/video → `sendPhoto` / `sendVideo`
- multiple files → `sendMediaGroup` (capped at 10, Telegram's own limit)

Files are uploaded directly from disk (multipart), not by URL, so
you're not bound by Telegram's ~5MB/20MB URL-fetch ceiling — bot
uploads go up to 50MB per file on the standard Bot API.

The original message is only deleted **after** a successful send. If
the download or send fails, you get a text message with the raw link
instead, and nothing is deleted — you never lose a post silently.

## Known risks worth testing for before relying on this unattended

- RedDownloader has a handful of open bug reports as of early 2026,
  and depends on `pytube` for YouTube-linked posts specifically —
  pytube breaks fairly often when YouTube changes things. Reddit
  image/video/gallery posts (your main case) don't touch pytube at
  all, so this mostly matters if your RSS feed ever includes
  YouTube-linked Reddit posts.
- Run `python fetch_and_send.py` locally (or trigger the workflow
  manually via `workflow_dispatch` — add that trigger if you want a
  manual test button) against a real post before trusting it fully.
- GitHub Actions on the free tier queues rather than drops concurrent
  runs, so a burst of RSS items will just process one after another,
  not fail.
