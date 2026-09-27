from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime


QUEUE_FILE = os.path.join("data", "queue.json")


class JobQueue:
    """Thread-safe, persistent FIFO queue for video render jobs."""

    def __init__(self, path: str = QUEUE_FILE):
        self.path = path
        self._lock = threading.RLock()
        self.jobs = []
        self.load()

    def load(self):
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.jobs = raw if isinstance(raw, list) else []
            except Exception:
                self.jobs = []
            for job in self.jobs:
                if job.get("status") in {"running", "validating", "downloading", "transcribing",
                                         "generating_script", "generating_voice", "rendering"}:
                    job["status"] = "pending"
                    job["status_text"] = "Chờ (khôi phục sau khi app đóng)"
            self.save()

    def save(self):
        with self._lock:
            folder = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.jobs, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def add(self, payload: dict) -> dict:
        with self._lock:
            job = deepcopy(payload)
            job.setdefault("id", uuid.uuid4().hex[:12])
            job.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
            job.setdefault("status", "pending")
            job.setdefault("status_text", "Chờ")
            job.setdefault("output_path", "")
            job.setdefault("error", "")
            self.jobs.append(job)
            self.save()
            return deepcopy(job)

    def get(self, job_id: str):
        with self._lock:
            for job in self.jobs:
                if job.get("id") == job_id:
                    return deepcopy(job)
        return None

    def update(self, job_id: str, **changes):
        with self._lock:
            for job in self.jobs:
                if job.get("id") == job_id:
                    job.update(deepcopy(changes))
                    self.save()
                    return deepcopy(job)
        return None

    def remove(self, job_id: str) -> bool:
        with self._lock:
            before = len(self.jobs)
            self.jobs = [j for j in self.jobs if j.get("id") != job_id]
            changed = len(self.jobs) != before
            if changed:
                self.save()
            return changed

    def move(self, job_id: str, delta: int) -> bool:
        with self._lock:
            idx = next((i for i, j in enumerate(self.jobs) if j.get("id") == job_id), -1)
            target = idx + delta
            if idx < 0 or target < 0 or target >= len(self.jobs):
                return False
            if self.jobs[idx].get("status") == "running" or self.jobs[target].get("status") == "running":
                return False
            self.jobs[idx], self.jobs[target] = self.jobs[target], self.jobs[idx]
            self.save()
            return True

    def next_pending(self):
        with self._lock:
            for job in self.jobs:
                if job.get("status") == "pending":
                    return deepcopy(job)
        return None

    def snapshot(self):
        with self._lock:
            return deepcopy(self.jobs)
