"""Tests de l'API HTTP.

Aucun test ne soumet d'URL valide : cela déclencherait un vrai téléchargement.
On vérifie les gardes d'entrée, pas le moteur.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from soundgrab import __version__
from soundgrab.config import DEFAULTS, LIMITS
from soundgrab.server import app


@pytest.fixture
def client():
    """Client se présentant comme la machine hôte, adresse d'origine comprise."""
    with TestClient(app, base_url="http://127.0.0.1:8731", client=("127.0.0.1", 54321)) as c:
        yield c


@pytest.fixture
def lan_client():
    """Client du réseau local : autorisé à consulter, pas à reconfigurer."""
    with TestClient(app, base_url="http://192.168.1.10:8731", client=("192.168.1.42", 54321)) as c:
        yield c


def test_health_reports_versions(client):
    body = client.get("/api/health").json()

    assert body["version"] == __version__
    assert body["ytdlp_version"]
    assert body["python"]


def test_static_files_are_revalidated(client):
    """Sans cet en-tête, une mise à jour de SoundGrab laisserait le navigateur
    servir l'ancienne interface depuis son cache."""
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_a_foreign_host_is_rejected():
    """Un site distant peut faire pointer son domaine vers 127.0.0.1 pour que
    ses scripts passent pour la même origine que SoundGrab. Il se présente alors
    avec son propre en-tête Host, seul indice qui le trahit."""
    with TestClient(app, base_url="http://piege.example.com") as remote:
        assert remote.get("/api/health").status_code == 421


def test_out_of_range_values_are_clamped(client):
    body = client.post("/api/config", json={"values": {"workers": 99, "port": 20}}).json()

    assert body["values"]["workers"] == LIMITS["workers"][1]
    assert body["values"]["port"] == LIMITS["port"][0]


def test_an_unreadable_number_keeps_the_previous_value(client):
    body = client.post("/api/config", json={"values": {"workers": "beaucoup"}}).json()

    assert body["values"]["workers"] == DEFAULTS["workers"]


def test_a_lan_client_cannot_reconfigure(lan_client):
    """Sans authentification, laisser réécrire `output_dir` depuis le réseau
    reviendrait à offrir une écriture arbitraire sur le disque de l'hôte."""
    response = lan_client.post("/api/config", json={"values": {"output_dir": "C:/ailleurs"}})

    assert response.status_code == 403


def test_a_lan_client_may_still_watch_the_queue(lan_client):
    """Le téléphone garde le droit de suivre la file et d'y ajouter des URL."""
    assert lan_client.get("/api/state").status_code == 200


def test_input_without_any_url_is_rejected(client):
    assert client.post("/api/jobs", json={"urls": "bonjour"}).status_code == 400


def test_cancelling_an_unknown_job_returns_404(client):
    assert client.post("/api/jobs/999999/cancel").status_code == 404
