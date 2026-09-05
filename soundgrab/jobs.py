"""Etat partage des telechargements, lu par l'interface via SSE."""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field, fields

# queued -> resolving -> downloading -> done | error | cancelled
ACTIVE_STATES = {"queued", "resolving", "downloading"}

MAX_ERRORS = 50
MAX_FILES = 500


@dataclass
class Job:
    id: int
    url: str
    status: str = "queued"
    title: str = ""
    uploader: str = ""
    kind: str = ""          # "morceau" | "playlist" | "profil"
    total: int = 0          # nombre de pistes annonce
    completed: int = 0      # reellement telechargees
    skipped: int = 0        # deja presentes (journal)
    failed: int = 0
    percent: float = 0.0    # progression globale du job
    current: str = ""       # titre de la piste en cours
    stage: str = ""         # "telechargement" | "conversion" | "pochette"
    current_percent: float = 0.0
    speed: str = ""
    eta: str = ""
    error: str = ""
    errors: list = field(default_factory=list)
    files: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    finished: float | None = None


# `files` est exclu du snapshot : la liste peut contenir des centaines de chemins,
# l'interface ne s'en sert pas, et le flux SSE la reserialiserait a chaque trame.
_SNAPSHOT_FIELDS = tuple(f.name for f in fields(Job) if f.name != "files")


class JobStore:
    """Dictionnaire de jobs protege par un verrou.

    `version` est incremente a chaque mutation : le flux SSE s'en sert pour
    n'emettre que quand quelque chose a reellement bouge.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[int, Job] = {}
        self._cancelled: set[int] = set()
        # Compteur propre a l'instance : un compteur de module ferait repartir
        # les identifiants la ou le store precedent s'etait arrete.
        self._ids = itertools.count(1)
        self.version = 0

    def add(self, url: str) -> Job:
        with self._lock:
            job = Job(id=next(self._ids), url=url)
            self._jobs[job.id] = job
            self.version += 1
            return job

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: int, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            self.version += 1

    def add_error(self, job_id: int, message: str, *, failure: bool = True) -> None:
        """Journalise un probleme sur un job.

        `failure=False` sert aux messages qui ne correspondent a aucune piste
        perdue -- ceux de la phase d'analyse, par exemple : les compter ferait
        afficher « 1 en echec » sur un job qui n'a jamais commence.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if message and len(job.errors) < MAX_ERRORS:
                job.errors.append(message)
            if failure:
                job.failed += 1
            self.version += 1

    def add_file(self, job_id: int, path: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if len(job.files) < MAX_FILES:
                job.files.append(path)
            self.version += 1

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ACTIVE_STATES:
                return False
            self._cancelled.add(job_id)
            if job.status == "queued":
                # Jamais demarre : on le clot tout de suite
                job.status = "cancelled"
                job.finished = time.time()
            self.version += 1
            return True

    def is_cancelled(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def clear_finished(self) -> int:
        with self._lock:
            done = [i for i, j in self._jobs.items() if j.status not in ACTIVE_STATES]
            for i in done:
                del self._jobs[i]
                self._cancelled.discard(i)
            self.version += 1
            return len(done)

    def snapshot(self) -> dict:
        """Copie serialisable de l'etat, du job le plus recent au plus ancien.

        Les listes sont recopiees : le verrou est relache avant que l'appelant
        ne serialise, et un worker pourrait entre-temps y ajouter une entree.
        """
        with self._lock:
            jobs = []
            for job in self._jobs.values():
                data = {name: getattr(job, name) for name in _SNAPSHOT_FIELDS}
                data["errors"] = list(job.errors)
                data["file_count"] = len(job.files)
                jobs.append(data)
        jobs.sort(key=lambda j: j["id"], reverse=True)
        return {
            "version": self.version,
            "jobs": jobs,
            "active": sum(1 for j in jobs if j["status"] in ACTIVE_STATES),
        }
