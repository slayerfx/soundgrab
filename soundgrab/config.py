"""Configuration persistante de SoundGrab."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
ARCHIVE_PATH = DATA_DIR / "archive.txt"
BUNDLED_BIN = ROOT / "bin"


def _default_output_dir() -> str:
    """Dossier musique de l'utilisateur, sur n'importe quelle plateforme.

    Sous Windows le dossier peut avoir ete deplace par l'utilisateur : la base
    de registre donne son emplacement reel, la deduire de ~/Music serait faux.
    """
    if os.name == "nt":
        try:
            import winreg

            subkey = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as handle:
                music, _ = winreg.QueryValueEx(handle, "My Music")
            if music:
                return str(Path(os.path.expandvars(music)) / "SoundGrab")
        except OSError:
            pass
    xdg = os.environ.get("XDG_MUSIC_DIR")  # Linux, quand il est configure
    if xdg:
        return str(Path(xdg) / "SoundGrab")
    return str(Path.home() / "Music" / "SoundGrab")


DEFAULTS = {
    # Racine de la bibliotheque. Les fichiers y sont ranges en
    # <artiste>/<playlist>/<NN - titre>.mp3
    "output_dir": _default_output_dir(),
    # "mp3" = reencodage 320k, "original" = on garde le flux tel quel
    "audio_format": "mp3",
    "audio_quality": "320",
    # Conserve aussi le fichier source a cote du mp3
    "keep_original": False,
    # Telechargements simultanes. Au-dela de 3, SoundCloud repond 429.
    "workers": 2,
    "concurrent_fragments": 4,
    # Journal des morceaux deja pris -> une playlist reprise ne retelecharge rien
    "use_archive": True,
    # Deduit artiste/titre depuis les titres "Artiste - Titre"
    "parse_artist_from_title": False,
    # "", "chrome", "firefox", "edge", "brave" : pour les likes/prives
    "cookies_browser": "",
    # Chemin ffmpeg force (vide = detection auto)
    "ffmpeg_location": "",
    "port": 8731,
    # True = accessible depuis le telephone sur le reseau local
    "lan_access": False,
}

# Bornes des reglages numeriques. Definies ici, elles servent de source unique
# a l'API qui valide les entrees et au moteur qui dimensionne ses threads.
LIMITS = {
    "workers": (1, 8),
    "concurrent_fragments": (1, 16),
    # Sous 1024 il faudrait les droits administrateur pour ouvrir le port.
    "port": (1024, 65535),
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        # Fichier illisible ou tronque : on repart des defauts plutot que de
        # refuser de demarrer pour un octet perdu.
        with contextlib.suppress(json.JSONDecodeError, OSError):
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def save_config(cfg: dict) -> dict:
    merged = load_config()
    # On n'accepte que les cles connues, pour ne pas polluer le fichier
    merged.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def _has_ffmpeg(folder: Path) -> bool:
    return (folder / "ffmpeg.exe").exists() or (folder / "ffmpeg").exists()


def _registry_path_dirs() -> list[Path]:
    """Dossiers du PATH lus dans le registre plutot que dans l'environnement.

    L'Explorateur Windows conserve souvent un PATH perime jusqu'a la
    reconnexion de session : un ffmpeg fraichement installe serait alors
    invisible pour un processus lance par double-clic. Le registre, lui,
    est toujours a jour.
    """
    if os.name != "nt":
        return []
    import winreg

    keys = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    )
    dirs: list[Path] = []
    for root, subkey in keys:
        try:
            with winreg.OpenKey(root, subkey) as handle:
                raw, _ = winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue
        for part in str(raw).split(";"):
            part = os.path.expandvars(part.strip().strip('"'))
            if part:
                dirs.append(Path(part))
    return dirs


def _winget_candidates() -> list[Path]:
    """Emplacement d'installation par defaut des paquets winget."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    base = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if not base.is_dir():
        return []
    try:
        return [match.parent for match in base.glob("*ffmpeg*/*/bin/ffmpeg.exe")]
    except OSError:
        return []


def find_ffmpeg(cfg: dict | None = None) -> str | None:
    """Retourne le dossier contenant ffmpeg, ou None s'il est introuvable."""
    cfg = cfg or load_config()

    forced = (cfg.get("ffmpeg_location") or "").strip()
    if forced:
        path = Path(forced)
        candidate = path if path.is_dir() else path.parent
        if _has_ffmpeg(candidate):
            return str(candidate)

    if _has_ffmpeg(BUNDLED_BIN):
        return str(BUNDLED_BIN)

    found = shutil.which("ffmpeg")
    if found:
        return str(Path(found).parent)

    for folder in (*_registry_path_dirs(), *_winget_candidates()):
        try:
            if _has_ffmpeg(folder):
                return str(folder)
        except OSError:
            continue  # lecteur reseau deconnecte, chemin invalide

    return None
