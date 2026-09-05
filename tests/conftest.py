"""Fixtures communes.

Le garde-fou important est `isolated_config` : sans lui, le moindre test qui
appelle `save_config` ecraserait la configuration reelle de l'utilisateur.
"""
from __future__ import annotations

import pytest

from soundgrab import config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirige la configuration persistante vers un dossier jetable.

    `load_config` et `save_config` relisent ces chemins dans les globales du
    module a chaque appel : les remplacer suffit a rediriger tout le monde,
    y compris les endpoints du serveur qui importent les fonctions par leur nom.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "ARCHIVE_PATH", tmp_path / "archive.txt")
    return tmp_path
