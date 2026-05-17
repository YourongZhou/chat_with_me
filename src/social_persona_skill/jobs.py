from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from typing import Any, Callable
import json
import traceback
import uuid


JobCallable = Callable[[Callable[[str], None]], dict[str, Any] | None]


@dataclass(slots=True)
class JobRecord:
    job_id: str
    job_type: str
    status: str
    created_at: str
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)


class JobManager:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobRecord] = {}
        self._lock = Lock()
        self._queue: Queue[tuple[str, JobCallable]] = Queue()
        self._worker = Thread(target=self._run_loop, name="persona-job-worker", daemon=True)
        self._worker.start()

    def submit(self, job_type: str, task: JobCallable) -> JobRecord:
        record = JobRecord(
            job_id=uuid.uuid4().hex[:12],
            job_type=job_type,
            status="queued",
            created_at=self._utc_now(),
        )
        with self._lock:
            self._jobs[record.job_id] = record
            self._write_metadata(record)
        self._append_log(record.job_id, f"[queued] {job_type}")
        self._queue.put((record.job_id, task))
        return record

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            return self._jobs[job_id]

    def read_log(self, job_id: str) -> str:
        path = self._log_path(job_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def list_jobs(self) -> list[JobRecord]:
        with self._lock:
            return sorted(
                self._jobs.values(),
                key=lambda item: (item.created_at, item.job_id),
                reverse=True,
            )

    def _run_loop(self) -> None:
        while True:
            job_id, task = self._queue.get()
            try:
                self._run_job(job_id, task)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str, task: JobCallable) -> None:
        record = self.get(job_id)
        record.status = "running"
        record.started_at = self._utc_now()
        self._write_metadata(record)
        self._append_log(job_id, f"[running] {record.job_type}")

        def logger(message: str) -> None:
            self._append_log(job_id, message.rstrip())

        try:
            result = task(logger) or {}
        except Exception as exc:
            record.status = "failed"
            record.finished_at = self._utc_now()
            record.error = str(exc).strip() or exc.__class__.__name__
            self._append_log(job_id, f"[failed] {record.error}")
            self._append_log(job_id, traceback.format_exc())
            self._write_metadata(record)
            return

        record.status = "succeeded"
        record.finished_at = self._utc_now()
        record.result = result
        self._append_log(job_id, "[succeeded]")
        self._write_metadata(record)

    def _metadata_path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def _log_path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.log"

    def _write_metadata(self, record: JobRecord) -> None:
        self._metadata_path(record.job_id).write_text(
            json.dumps(asdict(record), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _append_log(self, job_id: str, message: str) -> None:
        if not message:
            return
        with self._log_path(job_id).open("a", encoding="utf-8") as handle:
            handle.write(message)
            if not message.endswith("\n"):
                handle.write("\n")

    def _utc_now(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
