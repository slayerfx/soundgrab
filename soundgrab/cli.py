"""Lancement du serveur local et ouverture du navigateur.

Les messages console restent en ASCII : certaines consoles Windows heritees
levent UnicodeEncodeError sur les accents.
"""
from __future__ import annotations

import contextlib
import socket
import threading
import webbrowser

import uvicorn

from .config import LIMITS, find_ffmpeg, load_config
from .server import app


def lan_ip() -> str:
    """IP locale de la machine, via la route par defaut (aucun paquet emis)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main() -> None:
    cfg = load_config()
    low, high = LIMITS["port"]
    port = max(low, min(int(cfg.get("port", 8731)), high))
    lan = bool(cfg.get("lan_access"))
    host = "0.0.0.0" if lan else "127.0.0.1"  # noqa: S104 - expose volontairement
    local_url = f"http://127.0.0.1:{port}"

    print()
    print("  SoundGrab")
    print(f"  Interface      {local_url}")
    if lan:
        print(f"  Reseau local   http://{lan_ip()}:{port}")
        print("                 Sans authentification : reseau de confiance uniquement.")
    print(f"  Bibliotheque   {cfg['output_dir']}")
    if not find_ffmpeg(cfg):
        print("  ATTENTION      ffmpeg introuvable : pas de conversion MP3 ni de tags.")
        print("                 Corriger avec : winget install Gyan.FFmpeg")
        print("                 Ou indiquer son chemin dans les reglages.")
    print("  Ctrl+C pour arreter.")
    print()

    threading.Timer(1.0, lambda: webbrowser.open(local_url)).start()
    # Ctrl+C est la facon prevue d'arreter : pas de trace d'erreur pour ca.
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(app, host=host, port=port, log_level="warning")
