from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_CAPTION_TEMPLATE = (
    "Backup uploaded\n"
    "File: {filename}\n"
    "Size: {size_human}\n"
    "Server: {hostname}\n"
    "Public IP: {public_ip}\n"
    "Backup mtime: {file_modified_at}\n"
    "Sent at: {sent_at}"
)


def _parse_bool(value: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _parse_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _split_csv(value: str | None, *, default: Iterable[str]) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return tuple(default)
    return tuple(parts)


@dataclass(frozen=True)
class Settings:
    bale_bot_token: str
    bale_api_base: str
    bale_target_chat_id: str
    backup_dir: Path
    file_patterns: tuple[str, ...]
    recursive_scan: bool
    scan_interval_seconds: float
    stable_seconds: float
    request_timeout_seconds: float
    long_poll_timeout_seconds: int
    retry_backoff_seconds: float
    caption_template: str
    server_public_ip: str
    public_ip_lookup_enabled: bool
    public_ip_lookup_url: str
    public_ip_lookup_timeout_seconds: float
    max_file_size_mb: int
    startup_send_existing: bool
    clear_webhook_on_start: bool
    delete_uploading_older_than_hours: float
    lock_file: Path
    log_level: str

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


def load_dotenv(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and ((value[0] == value[-1]) and value[0] in {'"', "'"}):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_settings(dotenv_path: str = ".env") -> Settings:
    load_dotenv(Path(dotenv_path))

    token = os.getenv("BALE_BOT_TOKEN", "").strip()
    chat_id = os.getenv("BALE_TARGET_CHAT_ID", "").strip()

    if not token:
        raise ValueError("BALE_BOT_TOKEN is required")
    if not chat_id:
        raise ValueError("BALE_TARGET_CHAT_ID is required")

    backup_dir = Path(os.getenv("BACKUP_DIR", "/backup")).expanduser().resolve()

    return Settings(
        bale_bot_token=token,
        bale_api_base=os.getenv("BALE_API_BASE", "https://tapi.bale.ai").rstrip("/"),
        bale_target_chat_id=chat_id,
        backup_dir=backup_dir,
        file_patterns=_split_csv(
            os.getenv("BACKUP_FILE_PATTERNS"),
            default=(
                "*.bak",
                "*.backup",
                "*.dump",
                "*.gz",
                "*.sql",
                "*.sql.gz",
                "*.tar",
                "*.tar.gz",
                "*.xz",
                "*.zip",
                "*.zst",
            ),
        ),
        recursive_scan=_parse_bool(os.getenv("RECURSIVE_SCAN"), default=False),
        scan_interval_seconds=_parse_float(os.getenv("SCAN_INTERVAL_SECONDS"), default=5.0),
        stable_seconds=_parse_float(os.getenv("STABLE_SECONDS"), default=20.0),
        request_timeout_seconds=_parse_float(os.getenv("REQUEST_TIMEOUT_SECONDS"), default=120.0),
        long_poll_timeout_seconds=_parse_int(os.getenv("LONG_POLL_TIMEOUT_SECONDS"), default=30),
        retry_backoff_seconds=_parse_float(os.getenv("RETRY_BACKOFF_SECONDS"), default=10.0),
        caption_template=os.getenv("CAPTION_TEMPLATE", DEFAULT_CAPTION_TEMPLATE).replace("\\n", "\n"),
        server_public_ip=os.getenv("SERVER_PUBLIC_IP", "").strip(),
        public_ip_lookup_enabled=_parse_bool(os.getenv("PUBLIC_IP_LOOKUP_ENABLED"), default=True),
        public_ip_lookup_url=os.getenv("PUBLIC_IP_LOOKUP_URL", "https://api.ipify.org").strip(),
        public_ip_lookup_timeout_seconds=_parse_float(
            os.getenv("PUBLIC_IP_LOOKUP_TIMEOUT_SECONDS"),
            default=3.0,
        ),
        max_file_size_mb=_parse_int(os.getenv("MAX_FILE_SIZE_MB"), default=50),
        startup_send_existing=_parse_bool(os.getenv("STARTUP_SEND_EXISTING"), default=True),
        clear_webhook_on_start=_parse_bool(os.getenv("CLEAR_WEBHOOK_ON_START"), default=True),
        delete_uploading_older_than_hours=_parse_float(
            os.getenv("DELETE_UPLOADING_OLDER_THAN_HOURS"),
            default=24.0,
        ),
        lock_file=Path(os.getenv("LOCK_FILE", "/tmp/bale-backup-bot.lock")).expanduser(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
