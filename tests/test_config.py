"""Tests de la configuration persistante et de la détection de ffmpeg."""
from __future__ import annotations

import json
import os

from soundgrab import config
from soundgrab.config import DEFAULTS, LIMITS, find_ffmpeg, load_config, save_config


def _fake_ffmpeg(folder):
    """Crée un exécutable factice au nom attendu par la plateforme."""
    binary = folder / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    binary.touch()
    return binary


def test_defaults_are_used_when_no_file_exists():
    assert load_config() == DEFAULTS


def test_a_corrupted_file_does_not_block_startup():
    """Mieux vaut repartir des défauts qu'un outil qui refuse de s'ouvrir parce
    qu'un octet a été perdu."""
    config.CONFIG_PATH.write_text("{ ceci n'est pas du json", encoding="utf-8")

    assert load_config() == DEFAULTS


def test_saving_merges_with_what_is_already_there():
    save_config({"workers": 5})

    reloaded = load_config()
    assert reloaded["workers"] == 5
    assert reloaded["output_dir"] == DEFAULTS["output_dir"]


def test_unknown_keys_are_discarded():
    """Sans ce filtre, une clé abandonnée par une ancienne version survivrait
    indéfiniment dans le fichier de l'utilisateur."""
    saved = save_config({"tentative": "x", "workers": 3})

    assert "tentative" not in saved
    assert "tentative" not in json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["workers"] == 3


def test_every_numeric_setting_has_bounds():
    """Invariant structurel : un nombre sans bornes serait accepté tel quel par
    l'API — port 0 ou deux cents téléchargements simultanés compris."""
    numeric = {
        key for key, value in DEFAULTS.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }

    assert numeric == set(LIMITS)


def test_ffmpeg_is_found_through_the_forced_path(tmp_path):
    _fake_ffmpeg(tmp_path)

    assert find_ffmpeg({"ffmpeg_location": str(tmp_path)}) == str(tmp_path)


def test_the_forced_path_accepts_the_binary_itself(tmp_path):
    """On colle plus volontiers le chemin du binaire que celui de son dossier."""
    binary = _fake_ffmpeg(tmp_path)

    assert find_ffmpeg({"ffmpeg_location": str(binary)}) == str(tmp_path)


def test_a_stale_forced_path_does_not_hide_the_rest(tmp_path, monkeypatch):
    """Un réglage périmé ne doit pas masquer un ffmpeg présent dans le PATH."""
    elsewhere = tmp_path / "ailleurs"
    elsewhere.mkdir()
    _fake_ffmpeg(elsewhere)
    monkeypatch.setattr(config.shutil, "which", lambda _: str(elsewhere / "ffmpeg"))

    assert find_ffmpeg({"ffmpeg_location": str(tmp_path / "absent")}) == str(elsewhere)


def test_missing_ffmpeg_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BUNDLED_BIN", tmp_path / "bin-absent")
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    monkeypatch.setattr(config, "_registry_path_dirs", list)
    monkeypatch.setattr(config, "_winget_candidates", list)

    assert find_ffmpeg({"ffmpeg_location": ""}) is None
