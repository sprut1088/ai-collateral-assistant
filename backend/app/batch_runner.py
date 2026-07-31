"""Folder-based batch ingestion runtime for collateral email files."""

from __future__ import annotations

import hashlib
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SUPPORTED_EXTENSIONS = {".txt", ".msg"}


class BatchRunner:
    def __init__(
        self,
        runtime_root: str,
        interval_seconds: int,
        process_callback: Callable[..., dict],
        duplicate_check_callback: Callable[[str], bool],
    ):
        self.runtime_root = Path(runtime_root)
        self.interval_seconds = max(1, int(interval_seconds))
        self._process_callback = process_callback
        self._duplicate_check_callback = duplicate_check_callback

        self.inbox_dir = self.runtime_root / "inbox"
        self.processed_dir = self.runtime_root / "processed"
        self.failed_dir = self.runtime_root / "failed"
        self.duplicates_dir = self.runtime_root / "duplicates"

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._last_run_at: str | None = None
        self._last_error: str | None = None
        self._last_batch = {"processed": 0, "failed": 0, "duplicates": 0, "total_scanned": 0}
        self._totals = {"processed": 0, "failed": 0, "duplicates": 0}

        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for folder in (
            self.runtime_root,
            self.inbox_dir,
            self.processed_dir,
            self.failed_dir,
            self.duplicates_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def _move_file(self, src: Path, dst_dir: Path) -> Path:
        dst_dir.mkdir(parents=True, exist_ok=True)
        candidate = dst_dir / src.name
        if not candidate.exists():
            shutil.move(str(src), str(candidate))
            return candidate

        stem = src.stem
        suffix = src.suffix
        idx = 1
        while True:
            candidate = dst_dir / f"{stem}_{idx}{suffix}"
            if not candidate.exists():
                shutil.move(str(src), str(candidate))
                return candidate
            idx += 1

    def _set_last_error(self, message: str | None) -> None:
        with self._state_lock:
            self._last_error = message

    def _is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self.interval_seconds)

    def start(self) -> dict:
        if self._is_running():
            return self.status()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="batch-runner", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None
        return self.status()

    def shutdown(self) -> None:
        self.stop()

    def set_interval(self, seconds: int) -> dict:
        with self._state_lock:
            self.interval_seconds = max(1, int(seconds))
        return self.status()

    def reset_runtime_data(self, clear_inbox: bool = True) -> dict:
        with self._run_lock:
            self._ensure_dirs()
            removed_entries = 0
            target_dirs = [self.processed_dir, self.failed_dir, self.duplicates_dir]
            if clear_inbox:
                target_dirs.insert(0, self.inbox_dir)

            for folder in target_dirs:
                for child in folder.iterdir():
                    if child.is_file() or child.is_symlink():
                        child.unlink(missing_ok=True)
                        removed_entries += 1
                    elif child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                        removed_entries += 1

            with self._state_lock:
                self._last_run_at = None
                self._last_error = None
                self._last_batch = {
                    "processed": 0,
                    "failed": 0,
                    "duplicates": 0,
                    "total_scanned": 0,
                }
                self._totals = {"processed": 0, "failed": 0, "duplicates": 0}

        state = self.status()
        state["runtime_entries_deleted"] = removed_entries
        return state

    def run_once(self) -> dict:
        with self._run_lock:
            self._ensure_dirs()
            processed = 0
            failed = 0
            duplicates = 0
            total_scanned = 0

            files = [p for p in sorted(self.inbox_dir.iterdir(), key=lambda p: p.name.lower()) if p.is_file()]
            for path in files:
                total_scanned += 1
                suffix = path.suffix.lower()
                if suffix not in SUPPORTED_EXTENSIONS:
                    failed += 1
                    self._move_file(path, self.failed_dir)
                    continue

                try:
                    content = path.read_bytes()
                except Exception as exc:
                    failed += 1
                    self._move_file(path, self.failed_dir)
                    self._set_last_error(f"Could not read {path.name}: {exc}")
                    continue

                content_hash = hashlib.sha256(content).hexdigest()
                if self._duplicate_check_callback(content_hash):
                    duplicates += 1
                    self._move_file(path, self.duplicates_dir)
                    continue

                try:
                    self._process_callback(
                        filename=path.name,
                        content=content,
                        source_mode="batch",
                        source_path=str(path),
                        content_hash=content_hash,
                    )
                except Exception as exc:
                    failed += 1
                    self._move_file(path, self.failed_dir)
                    self._set_last_error(f"Failed processing {path.name}: {exc}")
                else:
                    processed += 1
                    self._move_file(path, self.processed_dir)

            with self._state_lock:
                self._last_run_at = datetime.now(timezone.utc).isoformat()
                if total_scanned > 0 and self._last_error and failed == 0:
                    self._last_error = None
                self._last_batch = {
                    "processed": processed,
                    "failed": failed,
                    "duplicates": duplicates,
                    "total_scanned": total_scanned,
                }
                self._totals["processed"] += processed
                self._totals["failed"] += failed
                self._totals["duplicates"] += duplicates

        return self.status()

    def status(self) -> dict:
        self._ensure_dirs()
        inbox_files = len([p for p in self.inbox_dir.iterdir() if p.is_file()])
        with self._state_lock:
            return {
                "running": self._is_running(),
                "interval_seconds": self.interval_seconds,
                "runtime_root": str(self.runtime_root),
                "inbox_dir": str(self.inbox_dir),
                "processed_dir": str(self.processed_dir),
                "failed_dir": str(self.failed_dir),
                "duplicates_dir": str(self.duplicates_dir),
                "inbox_file_count": inbox_files,
                "last_run_at": self._last_run_at,
                "last_error": self._last_error,
                "last_batch": dict(self._last_batch),
                "totals": dict(self._totals),
            }
