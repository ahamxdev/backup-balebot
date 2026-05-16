# Bale Backup Bot (Production Ready)

This service continuously watches a backup directory, uploads new backup files to Bale chat(s), and removes files after successful delivery.

## Features

- Watches `BACKUP_DIR` on a scan interval.
- Waits for files to become stable before upload (prevents partial uploads).
- Supports one or multiple recipients with `BALE_TARGET_CHAT_IDS`.
- Uses atomic rename (`.uploading`) to avoid race conditions.
- Retries safely on failures.
- Prevents duplicate sends in partial-failure scenarios.
- Deletes files only after successful send flow.
- Uses a lock file to prevent multiple service instances.

## Processing Flow

1. Scan `BACKUP_DIR` for matching patterns.
2. Ignore files that are still changing.
3. Rename file to `*.uploading` (claim step).
4. Send to all configured `chat_id` targets.
5. Mark sent state and delete uploaded file.
6. On restart, recover pending `.uploading` files safely.

## Project Structure

- `src/balebot_backup/`: main service and API client
- `scripts/get_chat_id.py`: helper to extract `chat_id` from `getUpdates`
- `systemd/bale-backup-bot.service`: production service template
- `.env.example`: configuration template

## Requirements

- Python `3.10+`
- A Bale bot token
- At least one valid target `chat_id`
- Read and write permissions for `BACKUP_DIR`

## Installation

```bash
cd /home/ahamxdev/Files/Workspace/personal/backup-balebot
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and set values.

### Required

```env
BALE_BOT_TOKEN=YOUR_BALE_BOT_TOKEN
BALE_TARGET_CHAT_IDS=123456789,987654321
```

Notes:
- `BALE_TARGET_CHAT_IDS` accepts comma-separated values.
- English comma `,` and Persian comma `،` are both supported.
- Legacy fallback still works: `BALE_TARGET_CHAT_ID` (single or comma-separated) is used only when `BALE_TARGET_CHAT_IDS` is empty.

### Main Optional Settings

- `BACKUP_DIR=/backup`
- `BACKUP_FILE_PATTERNS=*.bak,*.backup,*.dump,*.gz,*.sql,*.sql.gz,*.tar,*.tar.gz,*.xz,*.zip,*.zst`
- `SCAN_INTERVAL_SECONDS=5`
- `STABLE_SECONDS=20`
- `MAX_FILE_SIZE_MB=50`
- `STARTUP_SEND_EXISTING=true`
- `CLEAR_WEBHOOK_ON_START=true`
- `LOG_LEVEL=INFO`

### Caption Template

Default upload caption:

```text
Backup uploaded
File: db.sql.gz
Size: 12.34 MB
Server: my-server
Public IP: 203.0.113.10
Backup mtime: 2026-05-16 02:30:12 +0330
Sent at: 2026-05-16 02:30:35 +0330
```

Set custom format with `CAPTION_TEMPLATE`.

Available placeholders:

```text
{filename}
{file_size}
{size_human}
{hostname}
{public_ip}
{backup_dir}
{file_modified_at}
{sent_at}
```

## How To Get `chat_id`

1. Send any message to your bot in Bale.
2. Run:

```bash
python scripts/get_chat_id.py --env-file .env
```

3. Put returned ids in `BALE_TARGET_CHAT_IDS`.

Example:

```env
BALE_TARGET_CHAT_IDS=123456789,987654321
```

## Run Locally

```bash
source .venv/bin/activate
bale-backup-bot --env-file .env
```

## Run With systemd

Use `systemd/bale-backup-bot.service` as a template.

1. Update paths and user/group for your server.
2. Copy to `/etc/systemd/system/bale-backup-bot.service`.
3. Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bale-backup-bot.service
sudo systemctl status bale-backup-bot.service
journalctl -u bale-backup-bot.service -f
```

## Reliability Notes

- If one target fails and others succeed, the service tracks per-target send progress and retries only remaining targets.
- If file deletion fails after a successful send, the service retries deletion without resending the same file.
- On startup, `.uploading` pending files are recovered and handled safely.

## Troubleshooting

- `404 Bad Request: no such group or user`
  - The `chat_id` is invalid, or that user/group has not started the bot yet.
  - Send a message to the bot from each target account and refresh `chat_id`.

- Files are repeatedly retried
  - Check write permissions on `BACKUP_DIR`.
  - Ensure the service user can delete files from backup path.

- Bot sends nothing
  - Verify `BALE_BOT_TOKEN`, `BALE_TARGET_CHAT_IDS`, and file patterns.
  - Check logs with `journalctl -u bale-backup-bot.service -f`.

## Bale API Reference

- https://docs.bale.ai/
- Endpoint format: `https://tapi.bale.ai/bot<TOKEN>/<METHOD>`
