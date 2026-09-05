"""API locale + service des fichiers de l'interface."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import DEFAULTS, LIMITS, find_ffmpeg, load_config, save_config
from .downloader import Downloader
from .jobs import JobStore

WEB_DIR = Path(__file__).resolve().parent / "web"

# Le navigateur applique une fraicheur heuristique aux fichiers servis sans
# en-tete de cache : apres une mise a jour de SoundGrab, il continuerait
# d'afficher l'ancienne interface. `no-cache` n'interdit pas le cache, il
# impose une revalidation -- en local, c'est un 304 immediat.
NO_CACHE = {"Cache-Control": "no-cache"}


class RevalidatedStatics(StaticFiles):
    """StaticFiles, mais chaque reponse est revalidee aupres du serveur."""

    def file_response(self, *args, **kwargs) -> FileResponse:
        response = super().file_response(*args, **kwargs)
        response.headers.update(NO_CACHE)
        return response


LOOPBACK = {"127.0.0.1", "::1", "localhost"}

store = JobStore()
downloader = Downloader(store)

app = FastAPI(title="SoundGrab", docs_url=None, redoc_url=None)


def _hostname(header: str) -> str:
    """Nom d'hote seul, port et crochets IPv6 retires."""
    host = header.strip().lower()
    if host.startswith("["):  # litteral IPv6 : [::1]:8731
        end = host.find("]")
        return host[1:end] if end != -1 else host[1:]
    return host.rsplit(":", 1)[0] if ":" in host else host


def _host_allowed(header: str) -> bool:
    """N'accepte que les adresses IP locales, jamais un nom de domaine.

    Un site web peut faire pointer son propre domaine vers 127.0.0.1 (« DNS
    rebinding ») : le navigateur considere alors ses scripts comme etant de la
    meme origine que SoundGrab et peut piloter l'API. Refuser tout `Host` qui
    n'est pas une IP locale coupe cette voie, sans gener l'usage normal.
    """
    host = _hostname(header)
    if host in LOOPBACK:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _is_local(request: Request) -> bool:
    return request.client is not None and request.client.host in LOOPBACK


def _require_local(request: Request) -> None:
    """Reserve une action a la machine hote.

    Avec `lan_access`, l'API n'a aucune authentification. Laisser un client du
    reseau reecrire `output_dir` reviendrait a lui offrir une ecriture arbitraire
    sur le disque ; le telephone garde le droit de mettre en file, pas celui de
    reconfigurer.
    """
    if not _is_local(request):
        raise HTTPException(
            status_code=403,
            detail="Action réservée à la machine qui héberge SoundGrab.",
        )


@app.middleware("http")
async def reject_foreign_hosts(request: Request, call_next):
    if not _host_allowed(request.headers.get("host", "")):
        return JSONResponse(
            {"detail": "Hôte non autorisé."},
            status_code=421,  # Misdirected Request
        )
    return await call_next(request)


class SubmitBody(BaseModel):
    urls: str


class ConfigBody(BaseModel):
    values: dict


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers=NO_CACHE)


@app.get("/api/health")
def health() -> dict:
    cfg = load_config()
    ffmpeg = find_ffmpeg(cfg)
    out_dir = Path(cfg["output_dir"])
    try:
        # Si le dossier n'existe pas encore, on interroge la racine du disque.
        probe = out_dir if out_dir.exists() else Path(out_dir.anchor or ".")
        free_gb = round(shutil.disk_usage(probe).free / 1024**3, 1)
    except OSError:
        free_gb = None
    return {
        "version": __version__,
        "ffmpeg": ffmpeg,
        "ytdlp_version": getattr(yt_dlp.version, "__version__", "?"),
        "python": sys.version.split()[0],
        "output_dir": str(out_dir),
        "output_exists": out_dir.exists(),
        "free_gb": free_gb,
    }


@app.get("/api/config")
def get_config() -> dict:
    return {"values": load_config(), "defaults": DEFAULTS}


@app.post("/api/config")
def set_config(request: Request, body: ConfigBody) -> dict:
    _require_local(request)
    values = dict(body.values)
    for key, (low, high) in LIMITS.items():
        if key not in values:
            continue
        try:
            values[key] = max(low, min(int(values[key]), high))
        except (TypeError, ValueError):
            values.pop(key)  # saisie illisible : on conserve l'ancienne valeur
    return {"values": save_config(values)}


@app.get("/api/state")
def state() -> dict:
    return store.snapshot()


@app.post("/api/jobs")
def submit(body: SubmitBody) -> dict:
    # Une URL par ligne, ou collées à la suite séparées par des espaces.
    urls = [u.strip() for line in body.urls.splitlines() for u in line.split()]
    urls = [u for u in urls if u.startswith(("http://", "https://"))]
    if not urls:
        raise HTTPException(status_code=400, detail="Aucune URL valide dans la saisie.")
    created = [downloader.submit(u).id for u in urls]
    return {"created": created}


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: int) -> dict:
    if not store.cancel(job_id):
        raise HTTPException(status_code=404, detail="Job introuvable ou déjà terminé.")
    return {"ok": True}


@app.post("/api/jobs/clear")
def clear() -> dict:
    return {"removed": store.clear_finished()}


@app.post("/api/open")
def open_folder(request: Request) -> dict:
    """Ouvre la bibliothèque dans l'explorateur de fichiers.

    La fenêtre s'ouvrirait sur la machine hôte : déclenchée depuis un téléphone,
    l'action n'aurait aucun sens pour celui qui la demande.
    """
    _require_local(request)
    target = Path(load_config()["output_dir"])
    target.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(target))  # noqa: S606
        # Liste d'arguments, jamais de shell : le chemin ne peut pas etre
        # reinterprete. `open` et `xdg-open` s'appellent par leur nom, c'est
        # l'usage sur ces plateformes, et seul un client local a pu fixer
        # `output_dir` (voir _require_local).
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(target)])  # noqa: S603, S607
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/events")
async def events() -> StreamingResponse:
    """Flux SSE : on n'émet que quand la version du store a bougé."""

    async def generator():
        last_version = -1
        idle_ticks = 0
        while True:
            # `version` est un simple entier : le lire ne coute rien, alors qu'un
            # snapshot recopie tous les jobs. Construire le second pour ne
            # comparer que le premier gaspillait deux copies par seconde et par
            # client connecte.
            if store.version != last_version:
                snapshot = store.snapshot()
                last_version = snapshot["version"]
                idle_ticks = 0
                yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            else:
                idle_ticks += 1
                if idle_ticks >= 30:  # battement toutes les ~12 s
                    idle_ticks = 0
                    yield ": ping\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/static", RevalidatedStatics(directory=str(WEB_DIR)), name="static")
