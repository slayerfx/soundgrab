# SoundGrab

Téléchargeur de playlists SoundCloud avec interface web locale. Tu colles une URL,
il récupère le morceau, la playlist ou le profil entier en MP3 320 taggé, pochette
incluse, rangé proprement sur le disque.

Le moteur est [yt-dlp](https://github.com/yt-dlp/yt-dlp) : tout ce qu'il gère
fonctionne ici, SoundCloud comme YouTube, Bandcamp ou Mixcloud.

---

## Installation

Python 3.10 ou plus récent.

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Le projet s'installe en mode éditable : `soundgrab` devient aussi une commande.

Reste **ffmpeg**, qui est obligatoire : SoundCloud sert la plupart des morceaux en
flux HLS fragmentés, et sans ffmpeg il n'y a ni assemblage, ni conversion MP3, ni
tags, ni pochettes.

```
winget install Gyan.FFmpeg
```

Aucune reconnexion de session n'est nécessaire : la détection lit le `PATH` dans le
registre Windows et inspecte les emplacements winget, elle ne dépend donc pas du
`PATH` hérité par le processus. Le bandeau jaune en haut de l'interface disparaît
dès que ffmpeg est trouvé.

Si tu préfères ne rien installer sur le système, dépose `ffmpeg.exe` dans un dossier
`bin/` à la racine du projet — il sera trouvé automatiquement.

## Lancement

Double-clic sur `SoundGrab.bat`, ou depuis un terminal :

```
.venv\Scripts\python.exe run.py
```

Une fois le projet installé, `soundgrab` fait la même chose depuis n'importe où.

Le navigateur s'ouvre sur `http://127.0.0.1:8731`. La fenêtre de console doit rester
ouverte pendant les téléchargements.

## Utilisation

Colle une URL et appuie sur Entrée. Sont acceptés :

| URL | Résultat |
|---|---|
| `soundcloud.com/artiste/morceau` | un morceau |
| `soundcloud.com/artiste/sets/playlist` | la playlist entière |
| `soundcloud.com/artiste/tracks` | tous les morceaux de l'artiste |
| `soundcloud.com/artiste/likes` | tous ses likes (voir Cookies) |
| `soundcloud.com/artiste` | le profil complet |

Plusieurs URL à la fois : une par ligne. Le glisser-déposer d'un lien depuis le
navigateur lance le téléchargement directement.

### L'interface

Elle tient en trois fichiers servis tels quels — pas de framework, pas d'étape de
build, pas de police ni de script chargés depuis un CDN. L'outil s'affiche donc
correctement même sans connexion.

- **Progression en direct.** Le serveur pousse son état par [SSE](https://developer.mozilla.org/fr/docs/Web/API/Server-sent_events)
  et n'émet que lorsque quelque chose a bougé. Chaque carte montre l'étape en cours
  (analyse, téléchargement, conversion, pochette), le morceau traité, le débit et le
  temps restant. La progression est reprise dans le titre de l'onglet, lisible sans
  revenir sur la page.
- **Thème clair et sombre**, calé par défaut sur le réglage du système, forçable
  dans un sens ou dans l'autre et mémorisé d'une session à l'autre.
- **Clavier et glisser-déposer.** `Entrée` lance, `Maj+Entrée` ajoute une ligne,
  `Échap` referme les réglages ; un lien déposé depuis le navigateur part
  directement.
- **Accessibilité.** Les changements d'état sont annoncés aux lecteurs d'écran, mais
  pas la progression — l'annoncer noierait l'utilisateur sous des centaines de
  messages. Les barres portent `role="progressbar"`, le panneau replié est `inert`,
  et les animations disparaissent si le système demande à les réduire.
- **Reprise automatique.** Si le serveur s'arrête, une pastille « reconnexion »
  apparaît et le flux se rétablit seul au retour.

### Autres plateformes

YouTube, YouTube Music, Bandcamp et Mixcloud fonctionnent sans réglage particulier,
playlists comprises. Deux différences à connaître sur YouTube :

- **Le son est meilleur que sur SoundCloud** : Opus autour de 150 kbps, contre 128
  côté SoundCloud.
- **Les tags sont pauvres.** Une vidéo ordinaire n'expose ni artiste, ni titre, ni
  album : yt-dlp retombe sur le nom de la chaîne comme artiste, et les fichiers se
  rangent donc sous ce nom. L'option « Déduire l'artiste du titre » est bien plus
  utile ici que sur SoundCloud, la plupart des titres étant au format
  `Artiste - Titre`. Les liens **YouTube Music**, eux, renseignent correctement
  artiste, titre et album.

Les miniatures YouTube étant en 16:9, elles sont recadrées au centre en carré avant
d'être intégrées, sans quoi la jaquette s'afficherait en rectangle dans les lecteurs
et les logiciels DJ.

## Rangement des fichiers

```
Musique\SoundGrab\
  Artiste\
    Nom de la playlist\
      001 - Premier morceau.mp3
      002 - Deuxième morceau.mp3
    Morceau hors playlist.mp3
```

Le numéro de piste conserve l'ordre de la playlist, ce qui évite que les logiciels
DJ mélangent tout par ordre alphabétique.

## Réglages

Accessibles par le bouton **Réglages**, stockés dans `data/config.json`.

- **Dossier de destination** — racine de la bibliothèque.
- **Format** — MP3 320 kbps, ou flux d'origine sans réencodage. À noter : SoundCloud
  ne diffuse souvent que du 128 kbps, donc 320 est un plafond de conteneur, pas un
  gain de qualité réel. La qualité d'origine n'est disponible que si l'artiste a
  activé le téléchargement sur son morceau.
- **Débit du MP3** — 320 kbps par défaut. Sans effet si le format est réglé sur la
  qualité d'origine ; le champ se grise alors.
- **Téléchargements simultanés** — 2 par défaut. Au-delà de 3, SoundCloud commence
  à répondre `429 Too Many Requests`. Baisser cette valeur ne coupe pas les
  téléchargements en cours : les workers en trop se retirent une fois leur job fini.
- **Fragments en parallèle** — s'applique à l'intérieur d'un même morceau. SoundCloud
  sert du HLS découpé : monter ce nombre accélère une piste isolée, là où les
  téléchargements simultanés accélèrent une playlist.
- **Ne jamais retélécharger** — un journal (`data/archive.txt`) mémorise chaque
  morceau déjà pris. Relancer une playlist ne récupère donc que les nouveautés :
  c'est ce qui permet de resynchroniser un profil chaque semaine sans tout refaire.
- **Déduire l'artiste du titre** — découpe les titres du type `Artiste - Titre` pour
  remplir correctement le tag artiste. Désactivé par défaut, car un titre du type
  `01 - Intro` serait mal interprété.
- **Cookies du navigateur** — nécessaire pour les likes d'un compte privé ou les
  morceaux réservés aux abonnés. Lit les cookies du navigateur choisi, en local.
- **Accessible depuis le réseau local** — expose l'interface sur `0.0.0.0` pour la
  piloter depuis le téléphone. Demande un redémarrage, et une règle de pare-feu
  Windows sur le port choisi. Voir la section **Exposition réseau** plus bas.
- **Chemin de ffmpeg** — à ne renseigner que si la détection automatique échoue.
  Le dossier contenant `ffmpeg.exe` suffit, le chemin du binaire est accepté aussi.
- **Port** — 8731 par défaut. Redémarrage requis. Sous 1024, l'ouverture du port
  demanderait les droits administrateur.

## Exposition réseau

Sans `lan_access`, SoundGrab n'écoute que sur `127.0.0.1` : rien ne sort de la
machine. Deux protections s'appliquent malgré tout.

**En-tête `Host` validé.** Un site web peut faire pointer son propre domaine vers
`127.0.0.1` — c'est le *DNS rebinding* — pour que ses scripts soient considérés
comme de la même origine que SoundGrab et pilotent l'API. Sa requête se présente
alors avec son domaine dans l'en-tête `Host`, seul indice qui le trahit : tout
`Host` qui n'est pas une adresse IP locale est refusé.

**Actions réservées à la machine hôte.** Avec `lan_access`, l'API n'a aucune
authentification. Un client du réseau peut consulter la file et y ajouter des URL,
mais ni modifier la configuration ni ouvrir l'explorateur : laisser réécrire
`output_dir` depuis le réseau reviendrait à offrir une écriture arbitraire sur le
disque. Cela reste une machine ouverte sur un réseau — à n'activer qu'en confiance.

## Développement

```
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
```

Les tests couvrent `jobs.py` et `config.py` — état partagé, verrous, bornes,
persistance — plus les gardes d'entrée de l'API. Aucun ne touche au réseau ni à
ffmpeg, et une fixture `autouse` redirige la configuration vers un dossier
temporaire : lancer la suite ne peut pas écraser tes réglages.

La CI rejoue lint et tests sur Windows et Linux, en Python 3.10 et 3.13.

## Dépannage

**`429 Too Many Requests`** — baisse les téléchargements simultanés à 1 et attends
quelques minutes. SoundCloud limite par adresse IP.

**« URL non reconnue, morceau privé ou supprimé »** — le morceau est privé,
géo-bloqué, ou retiré. Pour un contenu privé auquel ton compte a accès, renseigne
les cookies du navigateur dans les réglages.

**Un morceau échoue au milieu d'une playlist** — le reste continue quand même. Le
détail est dans la liste dépliable « problèmes » de la carte du job.

**yt-dlp ne reconnaît plus SoundCloud** — le site change régulièrement. Mets à jour :

```
.venv\Scripts\python.exe -m pip install --upgrade yt-dlp
```

## Cadre d'usage

SoundCloud autorise le téléchargement quand l'artiste l'active sur son morceau, et
une large part du catalogue est sous licence Creative Commons. Au-delà, le
téléchargement relève des conditions d'utilisation du site et du droit d'auteur
applicable. L'outil ne contourne aucune protection : il utilise les mêmes flux que
le lecteur web.

## Structure

```
run.py                  lanceur pour un double-clic
pyproject.toml          métadonnées, dépendances, config ruff et pytest
soundgrab/
  cli.py                démarrage du serveur, ouverture du navigateur
  config.py             réglages persistants, bornes, détection de ffmpeg
  jobs.py               état des jobs, protégé par verrou, versionné pour le SSE
  downloader.py         file d'attente, workers, intégration yt-dlp
  server.py             API FastAPI, flux d'événements, gardes réseau
  web/
    index.html          structure, jeu d'icônes SVG, gabarit des cartes
    style.css           thèmes clair/sombre, mise en page
    app.js              rendu des jobs, réglages, flux SSE
    favicon.svg
tests/
  conftest.py           isolation de la configuration
  test_jobs.py          état partagé, annulation, bornes, concurrence
  test_config.py        persistance, clés inconnues, détection de ffmpeg
  test_api.py           validation des entrées, gardes réseau
data/                   (ignoré par git)
  config.json           réglages
  archive.txt           journal des morceaux déjà téléchargés
```

## Licence

MIT, voir [LICENSE](LICENSE).
