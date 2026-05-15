#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if v and ((v[0] == v[-1]) and v[0] in {'"', "'"}):
            v = v[1:-1]
        os.environ.setdefault(k, v)


def extract_chat_id(update: dict) -> str | None:
    message = update.get("message") or update.get("edited_message")
    if isinstance(message, dict):
        chat = message.get("chat")
        if isinstance(chat, dict) and "id" in chat:
            return str(chat["id"])

    callback = update.get("callback_query")
    if isinstance(callback, dict):
        message = callback.get("message")
        if isinstance(message, dict):
            chat = message.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                return str(chat["id"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Find Bale chat_id using getUpdates")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--offset", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))
    token = os.getenv("BALE_BOT_TOKEN", "").strip()
    api_base = os.getenv("BALE_API_BASE", "https://tapi.bale.ai").rstrip("/")

    if not token:
        print("BALE_BOT_TOKEN is required in env file", file=sys.stderr)
        return 1

    url = f"{api_base}/bot{token}/getUpdates"
    payload = {"timeout": args.timeout, "limit": 100}
    if args.offset is not None:
        payload["offset"] = args.offset

    response = requests.post(url, data=payload, timeout=args.timeout + 10)
    response.raise_for_status()
    body = response.json()

    if not body.get("ok"):
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 1

    updates = body.get("result", [])
    if not updates:
        print("No updates found. Send a message to the bot from your Bale account, then run again.")
        return 0

    print(f"Found {len(updates)} updates")
    chat_ids: set[str] = set()
    max_update_id: int | None = None

    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = max(update_id, max_update_id or update_id)
        chat_id = extract_chat_id(update)
        if chat_id:
            chat_ids.add(chat_id)

    if chat_ids:
        print("Possible chat_id values:")
        for item in sorted(chat_ids):
            print(f"- {item}")
    else:
        print("No chat_id found in updates payload:")
        print(json.dumps(updates, ensure_ascii=False, indent=2))

    if max_update_id is not None:
        print(f"Next offset suggestion: {max_update_id + 1}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
