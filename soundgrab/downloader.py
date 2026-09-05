"""Moteur de téléchargement : une file d'attente, N workers, yt-dlp en bibliothèque."""
from __future__ import annotations

import itertools
import queue
import re
import threading
import time
from pathlib import Path

import yt_dlp

from .config import ARCHIVE_PATH, DATA_DIR, LIMITS, find_ffmpeg, load_config
from .jobs import JobStore

try:  # présent depuis yt-dlp 2022.x, mais on ne veut pas en dépendre
    from yt_dlp.utils import DownloadCancelled
except ImportError:  # pragma: no cover
    class DownloadCancelled(Exception):
        pass

try:
    from yt_dlp.postprocessor.metadataparser import MetadataParserPP
except ImportError:  # pragma: no cover
    MetadataParserPP = None

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
# Appliquée aux titres "Artiste - Titre" quand l'option est active.
# Les trois tirets couverts : normal, demi-cadratin, cadratin.
TITLE_SPLIT = r"(?P<artist>.+?)\s+[-–—]\s+(?P<title>.+)"  # noqa: RUF001 - tirets voulus
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _fmt_speed(value) -> str:
    if not value:
        return ""
    mb = value / 1048576
    return f"{mb:.1f} Mo/s" if mb >= 1 else f"{value / 1024:.0f} Ko/s"


def _fmt_eta(value) -> str:
    if not value:
        return ""
    value = int(value)
    return f"{value // 60}:{value % 60:02d}" if value >= 60 else f"{value}s"


def _clean(message) -> str:
    """Message yt-dlp débarrassé de ses codes couleur, borné en longueur."""
    return ANSI_RE.sub("", str(message)).strip()[:400]


# Traduction des échecs d'analyse les plus courants. Rendre le message brut de
# yt-dlp serait exact mais illisible ; se contenter d'un texte générique serait
# lisible mais faux — une panne réseau n'est pas une URL invalide. On fait les
# deux : une phrase claire ici, le message d'origine dans le détail dépliable.
_PROBE_CAUSES = (
    (("connection refused", "failed to resolve", "getaddrinfo", "name or service",
      "temporary failure", "timed out", "connection reset"),
     "Serveur injoignable. Vérifie la connexion réseau."),
    (("http error 404", "not found", "410"),
     "Introuvable : le morceau a été supprimé, ou l'URL est erronée."),
    (("http error 401", "http error 403", "forbidden", "private", "requires authentication"),
     "Accès refusé : contenu privé ou réservé. Renseigne les cookies du navigateur "
     "dans les réglages."),
    (("http error 429", "too many requests"),
     "SoundCloud limite les requêtes. Attends quelques minutes, ou baisse le nombre "
     "de téléchargements simultanés."),
    (("unsupported url", "no suitable extractor"),
     "URL non prise en charge : ce site n'est pas géré par yt-dlp."),
    (("geo", "not available in your country"),
     "Contenu bloqué dans ce pays."),
)

_PROBE_FALLBACK = "Analyse impossible : URL non reconnue, morceau privé ou supprimé."


def _explain_probe_failure(reason: str) -> str:
    low = reason.lower()
    for needles, message in _PROBE_CAUSES:
        if any(needle in low for needle in needles):
            return message
    return _PROBE_FALLBACK


class _JobLogger:
    """Redirige les messages yt-dlp vers le journal d'erreurs du job concerné.

    `failure` distingue les erreurs qui coûtent une piste (téléchargement) de
    celles qui n'en coûtent aucune (analyse) : seules les premières incrémentent
    le compteur d'échecs affiché.
    """

    def __init__(self, store: JobStore, job_id: int, *, failure: bool = True) -> None:
        self.store = store
        self.job_id = job_id
        self.failure = failure

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        text = _clean(msg)
        if text:
            self.store.add_error(self.job_id, text, failure=self.failure)


class Downloader:
    def __init__(self, store: JobStore) -> None:
        self.store = store
        self._queue: queue.Queue[int] = queue.Queue()
        self._lock = threading.Lock()
        self._worker_count = 0
        self._surplus = 0  # workers a retirer une fois leur job courant fini
        self._names = itertools.count(1)

    # ------------------------------------------------------------ file d'attente

    def submit(self, url: str):
        job = self.store.add(url.strip())
        self._ensure_workers(int(load_config().get("workers", 2)))
        self._queue.put(job.id)
        return job

    def _ensure_workers(self, target: int) -> None:
        """Aligne le nombre de workers sur le réglage courant.

        À la hausse, les threads démarrent immédiatement. À la baisse, on ne peut
        pas interrompre un téléchargement en cours sans perdre le fichier : les
        workers en trop se retirent d'eux-mêmes une fois leur job terminé.
        """
        low, high = LIMITS["workers"]
        target = max(low, min(int(target), high))
        with self._lock:
            self._surplus = max(0, self._worker_count - target)
            while self._worker_count < target:
                self._worker_count += 1
                threading.Thread(
                    target=self._worker, daemon=True, name=f"soundgrab-{next(self._names)}"
                ).start()

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._run(job_id)
            except Exception as exc:  # filet de sécurité : un worker ne meurt jamais
                self.store.update(
                    job_id, status="error", error=_clean(exc), finished=time.time()
                )
            finally:
                self._queue.task_done()

            with self._lock:
                if self._surplus:
                    self._surplus -= 1
                    self._worker_count -= 1
                    return

    # ------------------------------------------------------------------ un job

    def _run(self, job_id: int) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        if self.store.is_cancelled(job_id):
            self.store.update(job_id, status="cancelled", finished=time.time())
            return

        cfg = load_config()
        self.store.update(job_id, status="resolving", stage="analyse")

        info, reason = self._probe(job.url, cfg, job_id)
        if not info:
            # Quand yt-dlp n'a pas levé, le motif est déjà passé par le logger :
            # on relit la dernière entrée du journal pour qualifier l'échec.
            logged = self.store.get(job_id)
            detail = reason or (logged.errors[-1] if logged and logged.errors else "")
            if reason:
                self.store.add_error(job_id, reason, failure=False)
            self.store.update(
                job_id,
                status="error",
                error=_explain_probe_failure(detail),
                stage="",
                finished=time.time(),
            )
            return

        is_playlist = info.get("_type") == "playlist"
        entries = info.get("entries")
        if entries is not None and not isinstance(entries, list):
            entries = list(entries)  # profils SoundCloud : générateur paginé
        total = info.get("playlist_count") or (len(entries) if entries else 0)
        if not is_playlist:
            total = 1

        url_low = job.url.lower()
        if not is_playlist:
            kind = "morceau"
        elif any(s in url_low for s in ("/likes", "/tracks", "/reposts")) or total > 60:
            kind = "profil"
        else:
            kind = "playlist"

        self.store.update(
            job_id,
            kind=kind,
            total=total,
            title=info.get("title") or info.get("playlist_title") or job.url,
            uploader=info.get("uploader") or info.get("playlist_uploader") or "",
            status="downloading",
            stage="téléchargement",
        )

        state = {"done": 0, "frac": 0.0, "last_push": 0.0}

        def overall() -> float:
            base = state["done"] + state["frac"]
            return round(min(100.0, base / max(total, 1) * 100), 1)

        def progress_hook(d):
            if self.store.is_cancelled(job_id):
                raise DownloadCancelled()
            filename = (d.get("filename") or "").lower()
            if filename.endswith(IMAGE_SUFFIXES):
                return  # pochette en cours, pas une piste
            status = d.get("status")
            title = (d.get("info_dict") or {}).get("title") or ""

            if status == "downloading":
                total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                state["frac"] = (got / total_bytes) if total_bytes else 0.0
                now = time.monotonic()
                if now - state["last_push"] < 0.15:
                    return  # le hook tire à chaque fragment : on limite la casse
                state["last_push"] = now
                self.store.update(
                    job_id,
                    current=title,
                    stage="téléchargement",
                    current_percent=round(state["frac"] * 100, 1),
                    speed=_fmt_speed(d.get("speed")),
                    eta=_fmt_eta(d.get("eta")),
                    percent=overall(),
                )
            elif status == "finished":
                state["done"] += 1
                state["frac"] = 0.0
                state["last_push"] = 0.0
                self.store.update(
                    job_id,
                    completed=state["done"],
                    current=title,
                    current_percent=100.0,
                    speed="",
                    eta="",
                    percent=overall(),
                )

        def pp_hook(d):
            if self.store.is_cancelled(job_id):
                raise DownloadCancelled()
            name = d.get("postprocessor") or ""
            if d.get("status") == "started":
                if "ExtractAudio" in name:
                    self.store.update(job_id, stage="conversion", speed="", eta="")
                elif "Thumbnail" in name or "Metadata" in name:
                    self.store.update(job_id, stage="pochette")
            elif d.get("status") == "finished" and name == "MoveFiles":
                path = (d.get("info_dict") or {}).get("filepath")
                if path and not str(path).lower().endswith(IMAGE_SUFFIXES):
                    self.store.add_file(job_id, str(path))

        opts = self._build_opts(cfg, is_playlist and total != 1)
        opts["progress_hooks"] = [progress_hook]
        opts["postprocessor_hooks"] = [pp_hook]
        opts["logger"] = _JobLogger(self.store, job_id)

        cancelled = False
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([job.url])
        except DownloadCancelled:
            cancelled = True
        except yt_dlp.utils.DownloadError as exc:
            self.store.add_error(job_id, _clean(exc))

        final = self.store.get(job_id)
        failed = final.failed if final else 0
        done = state["done"]
        skipped = max(0, total - done - failed)

        if cancelled:
            status = "cancelled"
        elif done == 0 and failed > 0:
            status = "error"
        else:
            status = "done"

        self.store.update(
            job_id,
            status=status,
            completed=done,
            skipped=skipped,
            percent=100.0 if status == "done" else overall(),
            current="",
            stage="",
            speed="",
            eta="",
            error="" if status != "error" else "Aucun morceau n'a pu être téléchargé.",
            finished=time.time(),
        )

    # -------------------------------------------------------- options yt-dlp

    def _probe(self, url: str, cfg: dict, job_id: int) -> tuple[dict | None, str]:
        """Énumération rapide : titre et nombre de pistes, sans rien télécharger.

        Retourne `(info, raison)`. La raison est vide en cas de succès ; sinon
        elle porte le message d'origine. Sans elle, une panne réseau, un proxy
        mal configuré ou un yt-dlp périmé seraient tous rapportés à l'utilisateur
        comme « URL non reconnue », ce qui l'enverrait chercher au mauvais endroit.
        """
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "logger": _JobLogger(self.store, job_id, failure=False),
        }
        self._apply_cookies(opts, cfg)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            return None, _clean(exc)
        # Avec ignoreerrors, yt-dlp renvoie None au lieu de lever : le motif est
        # alors deja passe par le logger, il n'y a rien de plus a dire ici.
        return info, ""

    def _build_opts(self, cfg: dict, as_playlist: bool) -> dict:
        out_dir = Path(cfg["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        if as_playlist:
            tmpl = (
                "%(playlist_uploader,uploader,artist|Divers)s/%(playlist_title|Playlist)s/"
                "%(playlist_index)03d - %(title)s.%(ext)s"
            )
        else:
            tmpl = "%(uploader,artist|Divers)s/%(title)s.%(ext)s"

        opts = {
            "paths": {"home": str(out_dir)},
            "outtmpl": {"default": tmpl},
            "format": "bestaudio/best",
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "windowsfilenames": True,
            "trim_file_name": 120,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 3,
            "concurrent_fragment_downloads": int(cfg.get("concurrent_fragments", 4)),
            "writethumbnail": True,
            # Sans ca, yt-dlp depose la miniature de la playlist elle-meme dans
            # le dossier ("000 - <playlist>.jpg") : elle n'est integree a aucun
            # morceau et ne fait qu'encombrer la bibliotheque.
            "allow_playlist_files": False,
            "keepvideo": bool(cfg.get("keep_original")),
            "postprocessors": self._postprocessors(cfg),
            # min(iw,ih) plutot que ih : une miniature en portrait ferait
            # echouer un simple crop=ih:ih. Les virgules doivent etre echappees,
            # sinon ffmpeg les lit comme un separateur de filtres.
            "postprocessor_args": {
                "thumbnailsconvertor+ffmpeg_o": [
                    "-c:v", "mjpeg",
                    "-vf", r"crop=min(iw\,ih):min(iw\,ih)",
                ]
            },
        }

        if cfg.get("use_archive"):
            opts["download_archive"] = str(ARCHIVE_PATH)
        ffmpeg = find_ffmpeg(cfg)
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
        self._apply_cookies(opts, cfg)
        return opts

    def _postprocessors(self, cfg: dict) -> list:
        pps = []
        if cfg.get("parse_artist_from_title") and MetadataParserPP is not None:
            pps.append(
                {
                    "key": "MetadataParser",
                    "when": "pre_process",
                    "actions": [(MetadataParserPP.Actions.INTERPRET, "title", TITLE_SPLIT)],
                }
            )
        if cfg.get("audio_format") == "mp3":
            pps.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(cfg.get("audio_quality", "320")),
                }
            )
        # Recadrage centre de la pochette en carre. Les miniatures YouTube sont
        # en 16:9 et donnent une jaquette rectangulaire dans les lecteurs ; les
        # pochettes SoundCloud etant deja carrees, l'operation y est neutre.
        pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        # L'ordre compte : extraction audio, puis tags, puis pochette.
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": False})
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        return pps

    @staticmethod
    def _apply_cookies(opts: dict, cfg: dict) -> None:
        browser = (cfg.get("cookies_browser") or "").strip().lower()
        if browser:
            opts["cookiesfrombrowser"] = (browser, None, None, None)
