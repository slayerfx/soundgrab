"""Tests de l'état partagé des téléchargements.

`JobStore` est le seul point où plusieurs threads écrivent en même temps : c'est
là qu'une régression coûte le plus cher, et c'est de la logique pure, sans réseau
ni ffmpeg.
"""
from __future__ import annotations

import threading

from soundgrab.jobs import MAX_ERRORS, MAX_FILES, JobStore


def test_ids_are_per_store():
    """Un compteur de module ferait démarrer le second store là où le premier
    s'est arrêté : invisible en production, faux quand même."""
    first, second = JobStore(), JobStore()

    assert first.add("http://a").id == 1
    assert first.add("http://b").id == 2
    assert second.add("http://c").id == 1


def test_new_job_is_queued_and_bumps_version():
    store = JobStore()
    before = store.version

    job = store.add("http://a")

    assert job.status == "queued"
    assert store.version > before


def test_update_sets_requested_fields():
    store = JobStore()
    job = store.add("http://a")

    store.update(job.id, status="downloading", percent=42.0)

    assert store.get(job.id).status == "downloading"
    assert store.get(job.id).percent == 42.0


def test_update_on_unknown_job_does_not_raise():
    """Un job peut disparaître entre deux trames ; un worker en retard ne doit
    pas mourir pour autant."""
    JobStore().update(999, status="done")


def test_cancelling_a_queued_job_closes_it_at_once():
    store = JobStore()
    job = store.add("http://a")

    assert store.cancel(job.id) is True
    assert store.get(job.id).status == "cancelled"
    assert store.get(job.id).finished is not None


def test_cancelling_a_running_job_only_flags_it():
    """Le worker doit constater l'annulation lui-même pour rendre la main
    proprement ; le clore ici laisserait un téléchargement orphelin."""
    store = JobStore()
    job = store.add("http://a")
    store.update(job.id, status="downloading")

    assert store.cancel(job.id) is True
    assert store.get(job.id).status == "downloading"
    assert store.is_cancelled(job.id) is True


def test_cancelling_a_finished_job_is_refused():
    store = JobStore()
    job = store.add("http://a")
    store.update(job.id, status="done")

    assert store.cancel(job.id) is False


def test_cancelling_an_unknown_job_is_refused():
    assert JobStore().cancel(404) is False


def test_an_error_counts_a_lost_track_by_default():
    store = JobStore()
    job = store.add("http://a")

    store.add_error(job.id, "boum")

    assert store.get(job.id).failed == 1


def test_a_probe_error_is_logged_without_being_counted():
    """Rien n'a encore été tenté : afficher « 1 en échec » sur un job qui n'a
    jamais démarré enverrait l'utilisateur chercher au mauvais endroit."""
    store = JobStore()
    job = store.add("http://a")

    store.add_error(job.id, "analyse impossible", failure=False)

    assert store.get(job.id).errors == ["analyse impossible"]
    assert store.get(job.id).failed == 0


def test_error_log_is_capped_but_the_counter_is_not():
    """Une playlist entièrement cassée ne doit pas faire enfler l'état sans fin,
    sans pour autant faire mentir le décompte affiché."""
    store = JobStore()
    job = store.add("http://a")

    for i in range(MAX_ERRORS + 10):
        store.add_error(job.id, f"erreur {i}")

    assert len(store.get(job.id).errors) == MAX_ERRORS
    assert store.get(job.id).failed == MAX_ERRORS + 10


def test_file_list_is_capped():
    store = JobStore()
    job = store.add("http://a")

    for i in range(MAX_FILES + 5):
        store.add_file(job.id, f"/musique/{i}.mp3")

    assert len(store.get(job.id).files) == MAX_FILES


def test_clearing_finished_jobs_spares_the_active_ones():
    store = JobStore()
    finished = store.add("http://fini")
    running = store.add("http://encours")
    store.update(finished.id, status="done")
    store.update(running.id, status="downloading")

    assert store.clear_finished() == 1
    assert [j["id"] for j in store.snapshot()["jobs"]] == [running.id]


def test_snapshot_lists_newest_first():
    """L'interface affiche la liste telle quelle : l'ordre est un contrat."""
    store = JobStore()
    ids = [store.add(f"http://{i}").id for i in range(3)]

    assert [j["id"] for j in store.snapshot()["jobs"]] == sorted(ids, reverse=True)


def test_snapshot_counts_active_jobs():
    store = JobStore()
    first = store.add("http://a")
    store.add("http://b")
    store.update(first.id, status="done")

    assert store.snapshot()["active"] == 1


def test_snapshot_replaces_the_file_list_with_a_count():
    """Elle peut peser des centaines de chemins dont l'interface ne fait rien,
    et le flux SSE la resérialiserait à chaque trame."""
    store = JobStore()
    job = store.add("http://a")
    store.add_file(job.id, "/musique/x.mp3")

    entry = store.snapshot()["jobs"][0]

    assert "files" not in entry
    assert entry["file_count"] == 1


def test_snapshot_copies_the_error_log():
    """Le verrou est relâché avant que l'appelant ne sérialise : partager la
    liste l'exposerait à une mutation concurrente en pleine lecture."""
    store = JobStore()
    job = store.add("http://a")
    store.add_error(job.id, "premier")

    entry = store.snapshot()["jobs"][0]
    store.add_error(job.id, "second")

    assert entry["errors"] == ["premier"]


def test_store_withstands_concurrent_writes():
    """Quatre workers écrivent pendant qu'ils lisent : c'est exactement ce que
    fait le vrai moteur, et le verrou doit tenir."""
    store = JobStore()
    job = store.add("http://a")

    def hammer():
        for _ in range(200):
            store.add_file(job.id, "/musique/x.mp3")
            store.snapshot()

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(store.get(job.id).files) == MAX_FILES
