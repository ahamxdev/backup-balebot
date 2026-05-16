from __future__ import annotations

import fcntl
import logging
import os
import signal
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests

from .bale_api import BaleAPIError, BaleClient
from .config import Settings

LOGGER = logging.getLogger(__name__)

UPLOADING_SUFFIX = ".uploading"
SENT_MARKER_SUFFIX = ".sentok"
PROGRESS_MARKER_SUFFIX = ".progress"


@dataclass
class _StabilityState:
    size: int
    mtime_ns: int
    stable_since: float


class FileStabilityTracker:
    def __init__(self, stable_seconds: float) -> None:
        self._stable_seconds = stable_seconds
        self._state: dict[Path, _StabilityState] = {}

    def is_ready(self, path: Path) -> bool:
        now = time.monotonic()
        stat = path.stat()
        current = _StabilityState(
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            stable_since=now,
        )

        previous = self._state.get(path)
        if previous is None:
            self._state[path] = current
            return False

        if previous.size != current.size or previous.mtime_ns != current.mtime_ns:
            self._state[path] = current
            return False

        elapsed = now - previous.stable_since
        return elapsed >= self._stable_seconds

    def mark_ready(self, path: Path) -> None:
        self._state.pop(path, None)

    def prune(self, existing_paths: set[Path]) -> None:
        for path in tuple(self._state):
            if path not in existing_paths:
                self._state.pop(path, None)


class SingleInstanceLock:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                f"another instance is already running (lock: {self._lock_path})"
            ) from exc

        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class BackupSenderService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = BaleClient(
            api_base=settings.bale_api_base,
            token=settings.bale_bot_token,
            timeout_seconds=settings.request_timeout_seconds,
        )
        self._tracker = FileStabilityTracker(settings.stable_seconds)
        self._stop = False
        self._startup_ignored: set[Path] = set()
        self._hostname = socket.gethostname()
        self._public_ip_cache: str | None = settings.server_public_ip or None

    def install_signal_handlers(self) -> None:
        def _handler(signum, _frame) -> None:
            LOGGER.info("signal=%s received, shutting down", signum)
            self._stop = True

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def run(self) -> None:
        if not self.settings.backup_dir.exists():
            raise FileNotFoundError(f"backup directory not found: {self.settings.backup_dir}")
        if not self.settings.backup_dir.is_dir():
            raise NotADirectoryError(f"backup directory is not a directory: {self.settings.backup_dir}")

        try:
            with SingleInstanceLock(self.settings.lock_file):
                self.install_signal_handlers()
                self._prepare_startup_ignore()
                self._recover_uploading_files()
                if self.settings.clear_webhook_on_start:
                    self._clear_webhook_best_effort()

                LOGGER.info(
                    "service started backup_dir=%s patterns=%s recursive_scan=%s target_chat_ids=%s",
                    self.settings.backup_dir,
                    self.settings.file_patterns,
                    self.settings.recursive_scan,
                    self.settings.bale_target_chat_ids,
                )

                while not self._stop:
                    try:
                        processed_count = self._process_cycle()
                        if processed_count == 0:
                            time.sleep(self.settings.scan_interval_seconds)
                    except Exception:
                        LOGGER.exception("unexpected error in main loop")
                        time.sleep(self.settings.retry_backoff_seconds)
        finally:
            self.client.close()
            LOGGER.info("service stopped")

    def _prepare_startup_ignore(self) -> None:
        if self.settings.startup_send_existing:
            return
        self._startup_ignored = set(self._list_candidate_files())
        LOGGER.info("startup_send_existing=false, ignoring %s existing files", len(self._startup_ignored))

    def _clear_webhook_best_effort(self) -> None:
        try:
            cleared = self.client.delete_webhook()
            LOGGER.info("deleteWebhook called result=%s", cleared)
        except BaleAPIError:
            LOGGER.exception("deleteWebhook failed (continuing)")

    def _iter_files_for_pattern(self, pattern: str) -> Iterable[Path]:
        if self.settings.recursive_scan:
            yield from self.settings.backup_dir.rglob(pattern)
            return
        yield from self.settings.backup_dir.glob(pattern)

    def _list_candidate_files(self) -> list[Path]:
        files: dict[Path, None] = {}
        for pattern in self.settings.file_patterns:
            for path in self._iter_files_for_pattern(pattern):
                if not path.is_file():
                    continue
                if path.name.endswith(UPLOADING_SUFFIX):
                    continue
                files[path] = None

        sorted_files = sorted(
            files.keys(),
            key=lambda item: (item.stat().st_mtime_ns, item.name),
        )
        return sorted_files

    def _recover_uploading_files(self) -> None:
        now = time.time()
        max_age_seconds = self.settings.delete_uploading_older_than_hours * 3600
        for path in self._find_uploading_files():
            sent_marker = self._sent_marker_path(path)
            if sent_marker.exists():
                # This file was already sent successfully. Keep retrying deletion only.
                self._delete_sent_uploading(path=path, sent_marker=sent_marker)
                continue

            try:
                age = now - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age >= max_age_seconds:
                try:
                    path.unlink(missing_ok=True)
                    self._sent_marker_path(path).unlink(missing_ok=True)
                    self._progress_marker_path(path).unlink(missing_ok=True)
                    LOGGER.warning("deleted stale pending file %s", path)
                except OSError:
                    LOGGER.exception("failed to delete stale pending file %s", path)

    def _sent_marker_path(self, uploading_path: Path) -> Path:
        return uploading_path.with_name(uploading_path.name + SENT_MARKER_SUFFIX)

    def _mark_sent_pending_delete(self, uploading_path: Path) -> Path:
        marker = self._sent_marker_path(uploading_path)
        try:
            marker.write_text(_format_timestamp(time.time()), encoding="utf-8")
        except OSError:
            LOGGER.exception("failed to write sent marker for %s", uploading_path)
        return marker

    def _delete_sent_uploading(self, path: Path, sent_marker: Path) -> bool:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            LOGGER.exception("file was sent before but delete retry failed path=%s", path)
            return False

        try:
            sent_marker.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("delete succeeded but sent marker remove failed marker=%s", sent_marker)
        try:
            self._progress_marker_path(path).unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("delete succeeded but progress marker remove failed for path=%s", path)

        LOGGER.info("cleaned already-sent file path=%s", path)
        return True

    def _progress_marker_path(self, uploading_path: Path) -> Path:
        return uploading_path.with_name(uploading_path.name + PROGRESS_MARKER_SUFFIX)

    def _load_sent_targets(self, uploading_path: Path) -> set[str]:
        marker = self._progress_marker_path(uploading_path)
        if not marker.exists():
            return set()
        try:
            raw = marker.read_text(encoding="utf-8").strip()
        except OSError:
            LOGGER.exception("failed reading progress marker path=%s", marker)
            return set()

        if not raw:
            return set()
        normalized = raw.replace("،", ",").replace(";", ",").replace("\n", ",")
        return {part.strip() for part in normalized.split(",") if part.strip()}

    def _save_sent_targets(self, uploading_path: Path, sent_targets: set[str]) -> None:
        marker = self._progress_marker_path(uploading_path)
        if not sent_targets:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("failed deleting empty progress marker path=%s", marker)
            return

        try:
            marker.write_text(",".join(sorted(sent_targets)), encoding="utf-8")
        except OSError:
            LOGGER.exception("failed writing progress marker path=%s", marker)

    def _find_uploading_files(self) -> Iterable[Path]:
        pattern = f"*{UPLOADING_SUFFIX}"
        if self.settings.recursive_scan:
            yield from self.settings.backup_dir.rglob(pattern)
        else:
            yield from self.settings.backup_dir.glob(pattern)

    def _list_uploading_files(self) -> list[Path]:
        files = [path for path in self._find_uploading_files() if path.is_file()]
        return sorted(files, key=lambda item: (item.stat().st_mtime_ns, item.name))

    def _process_uploading_files(self) -> int:
        processed_count = 0
        now = time.time()
        max_age_seconds = self.settings.delete_uploading_older_than_hours * 3600

        for uploading_path in self._list_uploading_files():
            if self._stop:
                break

            sent_marker = self._sent_marker_path(uploading_path)
            if sent_marker.exists():
                if self._delete_sent_uploading(path=uploading_path, sent_marker=sent_marker):
                    processed_count += 1
                continue

            try:
                age = now - uploading_path.stat().st_mtime
            except FileNotFoundError:
                continue

            if age >= max_age_seconds:
                try:
                    uploading_path.unlink(missing_ok=True)
                    self._sent_marker_path(uploading_path).unlink(missing_ok=True)
                    self._progress_marker_path(uploading_path).unlink(missing_ok=True)
                    LOGGER.warning("deleted stale pending file %s", uploading_path)
                except OSError:
                    LOGGER.exception("failed to delete stale pending file %s", uploading_path)
                continue

            try:
                sent = self._send_uploading_then_delete(uploading_path)
            except BaleAPIError:
                LOGGER.exception("failed to resume pending upload %s", uploading_path)
                time.sleep(self.settings.retry_backoff_seconds)
                continue
            except Exception:
                LOGGER.exception("unexpected failure while resuming pending upload %s", uploading_path)
                time.sleep(self.settings.retry_backoff_seconds)
                continue

            if sent:
                processed_count += 1

        return processed_count

    def _process_cycle(self) -> int:
        processed_count = self._process_uploading_files()
        files = self._list_candidate_files()
        existing = set(files)
        self._tracker.prune(existing)

        for file_path in files:
            if self._stop:
                break

            if file_path in self._startup_ignored:
                continue

            if not self._tracker.is_ready(file_path):
                continue

            try:
                sent = self._send_then_delete(file_path)
            except BaleAPIError:
                LOGGER.exception("failed to send %s", file_path)
                time.sleep(self.settings.retry_backoff_seconds)
                continue
            except Exception:
                LOGGER.exception("unexpected failure while processing %s", file_path)
                time.sleep(self.settings.retry_backoff_seconds)
                continue

            if sent:
                processed_count += 1

        return processed_count

    def _send_then_delete(self, path: Path) -> bool:
        if not path.exists():
            return False

        file_stat = path.stat()
        file_size = file_stat.st_size
        if file_size > self.settings.max_file_size_bytes:
            LOGGER.error(
                "skipping file bigger than max size file=%s size=%s max=%s",
                path,
                file_size,
                self.settings.max_file_size_bytes,
            )
            self._tracker.mark_ready(path)
            return False

        caption = self._build_caption(
            path=path,
            file_size=file_size,
            file_mtime=file_stat.st_mtime,
        )
        sending_path = path.with_name(path.name + UPLOADING_SUFFIX)
        try:
            path.rename(sending_path)
        except FileNotFoundError:
            return False
        except OSError:
            LOGGER.exception("failed to claim file for upload: %s", path)
            return False

        LOGGER.info("sending file=%s size=%s", path.name, file_size)
        try:
            self._send_to_targets(sending_path=sending_path, original_filename=path.name, caption=caption)
        except BaleAPIError:
            if not path.exists() and sending_path.exists():
                try:
                    sending_path.rename(path)
                except OSError:
                    LOGGER.exception("failed to rollback pending file name for %s", sending_path)
            sent_marker = self._sent_marker_path(sending_path)
            try:
                sent_marker.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("failed to remove sent marker after rollback marker=%s", sent_marker)
            raise

        sent_marker = self._mark_sent_pending_delete(sending_path)
        if not self._delete_sent_uploading(path=sending_path, sent_marker=sent_marker):
            return False

        self._tracker.mark_ready(path)
        self._startup_ignored.discard(path)
        return True

    def _send_uploading_then_delete(self, uploading_path: Path) -> bool:
        if not uploading_path.exists():
            return False

        original_filename = uploading_path.name[: -len(UPLOADING_SUFFIX)]
        original_path = uploading_path.with_name(original_filename)
        file_stat = uploading_path.stat()
        file_size = file_stat.st_size
        if file_size > self.settings.max_file_size_bytes:
            LOGGER.error(
                "skipping pending file bigger than max size file=%s size=%s max=%s",
                uploading_path,
                file_size,
                self.settings.max_file_size_bytes,
            )
            return False

        caption = self._build_caption(
            path=original_path,
            file_size=file_size,
            file_mtime=file_stat.st_mtime,
        )

        LOGGER.info("resuming pending file=%s size=%s", original_filename, file_size)
        self._send_to_targets(
            sending_path=uploading_path,
            original_filename=original_filename,
            caption=caption,
        )

        sent_marker = self._mark_sent_pending_delete(uploading_path)
        if not self._delete_sent_uploading(path=uploading_path, sent_marker=sent_marker):
            return False

        self._tracker.mark_ready(original_path)
        self._startup_ignored.discard(original_path)
        return True

    def _send_to_targets(self, sending_path: Path, original_filename: str, caption: str) -> None:
        sent_targets = self._load_sent_targets(sending_path)
        for chat_id in self.settings.bale_target_chat_ids:
            if chat_id in sent_targets:
                LOGGER.info("skip already-sent target file=%s chat_id=%s", original_filename, chat_id)
                continue
            try:
                result = self.client.send_document(
                    chat_id=chat_id,
                    file_path=sending_path,
                    caption=caption,
                    filename=original_filename,
                )
            except BaleAPIError as exc:
                self._save_sent_targets(sending_path, sent_targets)
                raise BaleAPIError(
                    f"failed sending file={original_filename} to chat_id={chat_id}: {exc}"
                ) from exc
            message_id = result.get("message_id")
            LOGGER.info("sent file=%s chat_id=%s message_id=%s", original_filename, chat_id, message_id)
            sent_targets.add(chat_id)
            self._save_sent_targets(sending_path, sent_targets)

        self._save_sent_targets(sending_path, set())

    def _build_caption(self, path: Path, file_size: int, file_mtime: float) -> str:
        return self.settings.caption_template.format(
            filename=path.name,
            file_size=file_size,
            size_human=_format_bytes(file_size),
            hostname=self._hostname,
            public_ip=self._get_public_ip(),
            backup_dir=str(self.settings.backup_dir),
            file_modified_at=_format_timestamp(file_mtime),
            sent_at=_format_timestamp(time.time()),
        )

    def _get_public_ip(self) -> str:
        if self._public_ip_cache:
            return self._public_ip_cache
        if not self.settings.public_ip_lookup_enabled:
            return "unknown"

        try:
            response = requests.get(
                self.settings.public_ip_lookup_url,
                timeout=self.settings.public_ip_lookup_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.warning("failed to resolve public IP: %s", exc)
            return "unknown"

        public_ip = response.text.strip()
        if not public_ip:
            return "unknown"

        self._public_ip_cache = public_ip
        return public_ip


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
