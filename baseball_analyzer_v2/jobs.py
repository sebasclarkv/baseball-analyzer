"""Bounded local background work for long historical evaluations."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import time
from uuid import uuid4

POOL = ThreadPoolExecutor(max_workers=1)
LOCK = Lock()
JOBS = {}


def submit(action, *, error_message="No se pudo evaluar el historial. Revisa acceso a Statcast y datos disponibles."):
    with LOCK:
        for key in list(JOBS):
            if JOBS[key]["status"] in {"complete", "error"} and time() - JOBS[key]["created"] > 3600:
                del JOBS[key]
        if sum(j["status"] in {"queued", "running"} for j in JOBS.values()) >= 3:
            raise ValueError("Ya hay tres evaluaciones pendientes; espera a que termine una.")
        job_id = uuid4().hex
        JOBS[job_id] = {"id": job_id, "status": "queued", "completed": 0, "total": None, "created": time()}
    def update(done, total):
        with LOCK:
            JOBS[job_id].update(completed=done, total=total)
    def run():
        with LOCK:
            JOBS[job_id]["status"] = "running"
        try:
            result = action(update)
            with LOCK:
                JOBS[job_id].update(status="complete", result=result)
        except Exception:
            with LOCK:
                JOBS[job_id].update(status="error", message=error_message)
    POOL.submit(run)
    return {"id": job_id, "status": "queued"}


def get(job_id):
    with LOCK:
        return dict(JOBS[job_id]) if job_id in JOBS else None
