# Sonauto Downloader

Téléchargez, organisez et écoutez tous vos sons likés sur [Sonauto.ai](https://sonauto.ai) depuis une interface web locale, style Spotify.

---

## Technologies

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)
![Uvicorn](https://img.shields.io/badge/Uvicorn-ASGI-4B32C3?style=flat-square&logo=gunicorn&logoColor=white)
![HTTPX](https://img.shields.io/badge/HTTPX-async-0072B1?style=flat-square)
![Mutagen](https://img.shields.io/badge/Mutagen-ID3_tags-E53E3E?style=flat-square)
![OpenCV](https://img.shields.io/badge/OpenCV-inpainting-5C3EE8?style=flat-square&logo=opencv&logoColor=white)
![Pillow](https://img.shields.io/badge/Pillow-image-3F7CAC?style=flat-square)
![Supabase](https://img.shields.io/badge/Supabase-PostgREST-3ECF8E?style=flat-square&logo=supabase&logoColor=white)
![HTML5](https://img.shields.io/badge/HTML5-CSS3-E34F26?style=flat-square&logo=html5&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-Vanilla-F7DF1E?style=flat-square&logo=javascript&logoColor=black)

---

## Fonctionnalités

### Téléchargement
- Téléchargement automatique de tous les sons likés (pagination complète Supabase)
- Téléchargement via URL directe de l'éditeur Sonauto
- Liste noire des sons supprimés — jamais re-téléchargés automatiquement
- Protection anti-doublons avec noms uniques (`Son (2).mp3`, etc.)

### Tags ID3 automatiques
- Titre, artiste, genre, année extraits de l'API Sonauto
- Paroles non synchronisées (USLT) et synchronisées mot par mot (SYLT)
- Fichiers `.lrc` générés pour les players compatibles
- Resync complet en un clic : complète tous les tags manquants (artiste, type, paroles, genre, année)

### Interface style Spotify
- **Page artiste** : bannière, biographie, sons populaires triés par écoutes, discographie
- **Page sortie** : tracklist avec couverture floutée en arrière-plan, compteur de lectures
- **Paroles fullscreen** : texte large à gauche, infos artiste à droite — style Spotify
- **Player persistant** : barre de lecture commune à toutes les pages via app shell
- **Compteur de lectures** : nombre d'écoutes par son, sauvegardé localement

### Gestion des albums
- Créer des albums, EPs et singles avec couverture, type et numéros de piste
- Modifier un album existant depuis l'interface
- Trier les pistes par drag & drop
- Appliquer une cover ou un artiste à toutes les pistes d'un coup
- Sorties populaires triées par nombre d'écoutes cumulées

### Éditeur de tags
- Modifier titre, artiste, album, année, genre, numéro de piste, cover art
- Supprimer définitivement un son (ajout automatique à la liste noire)
- Viewer LRC : affichage synchronisé des paroles avec auto-scroll

### Suppression de filigrane
- Script Python pour effacer le logo Sonauto des covers via inpainting OpenCV
- Détection automatique dans les 4 coins
- Traitement par lot de tout le dossier `musiques/`

---

## Installation

```bash
git clone https://github.com/jocha28/down_music.git
cd down_music

pip install -r requirements.txt
pip install pillow opencv-python-headless   # pour supprimer_filigrane.py
```

### Lancer le serveur

```bash
python3 main.py
# ou
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Ouvrir [http://localhost:8000](http://localhost:8000)

---

## Connexion

1. Se connecter sur [sonauto.ai](https://sonauto.ai)
2. Ouvrir la console F12 → onglet **Réseau**
3. Trouver une requête vers `/api/...` et copier le header `Authorization: Bearer <token>`
4. Coller le token dans le champ de connexion de l'interface

Le token est automatiquement renouvelé via le `refresh_token` Supabase.

---

## Interface web

| Page | URL | Description |
|------|-----|-------------|
| Bibliothèque | `/` | Sons likés, téléchargement, suivi en temps réel |
| Page artiste | `/static/artiste.html?artiste=Jocha` | Profil artiste style Spotify |
| Page sortie | `/static/sortie.html?artiste=Jocha&album=BUG ROYAL` | Album / EP / Single |
| Éditeur de tags | `/static/editeur.html` | Modifier les tags ID3 |
| Gestionnaire d'albums | `/static/album.html` | Créer et modifier des albums |
| Swagger API | `/swagger` | Documentation interactive de l'API |

---

## API — Endpoints principaux

```text
GET    /api/sons                          → sons likés depuis Sonauto
POST   /api/telecharger/{generation_id}   → télécharger un son
POST   /api/telecharger/tous              → tout télécharger
POST   /api/telecharger/url               → télécharger via URL éditeur Sonauto
GET    /api/taches/{id}/flux              → progression SSE en temps réel
GET    /api/taches                        → liste de toutes les tâches

GET    /api/fichiers                      → liste des MP3 locaux
GET    /api/fichiers/{nom}                → stream du fichier MP3
GET    /api/fichiers/{nom}/cover          → cover art (image)
GET    /api/fichiers/{nom}/lyrics         → paroles LRC
GET    /api/fichiers/{nom}/tags           → lire les tags ID3
POST   /api/fichiers/{nom}/tags           → écrire les tags ID3
DELETE /api/fichiers/{nom}                → supprimer (+ ajout liste noire)

GET    /api/suppressions                  → titres supprimés (liste noire)
POST   /api/suppressions/nettoyer         → supprimer du disque les fichiers revenus

POST   /api/tags/synchroniser             → resync complet (artiste, genre, année, type, paroles)

GET    /api/albums                        → tous les albums
GET    /api/albums/{nom}                  → pistes d'un album

GET    /api/artistes                      → liste des artistes
GET    /api/artistes/{nom}/profil         → sons et albums d'un artiste
GET    /api/artistes/{nom}/info           → profil complet (bio, photo, etc.)
POST   /api/artistes/{nom}/info           → sauvegarder le profil artiste
```

---

## Suppression du filigrane

Les covers générées par Sonauto.ai contiennent un logo dans les coins. Pour le supprimer :

```bash
# Tous les MP3 du dossier musiques/ — tous les coins
python3.13 supprimer_filigrane.py --coin tous

# Un seul fichier
python3.13 supprimer_filigrane.py "Active.mp3"

# Une image directement (JPG/PNG)
python3.13 supprimer_filigrane.py cover.jpg --coin bd
```

Options `--coin` : `bd` (bas-droite, défaut), `bg`, `hd`, `hg`, `tous`

---

## Structure du projet

```text
.
├── main.py                        # Point d'entrée FastAPI + lifespan
├── api/
│   ├── routes.py                  # Toutes les routes API
│   ├── client_sonauto.py          # Client HTTP Sonauto / Supabase
│   ├── service_telechargement.py  # Logique de téléchargement + tags ID3
│   └── modeles.py                 # Modèles Pydantic
├── static/
│   ├── index.html                 # Bibliothèque + téléchargements
│   ├── artiste.html               # Page artiste style Spotify
│   ├── sortie.html                # Page album / EP / single
│   ├── editeur.html               # Éditeur de tags ID3
│   ├── album.html                 # Gestionnaire d'albums
│   ├── shell.html                 # App shell avec player persistant
│   └── profil_editeur.html        # Éditeur de profil artiste
├── data/
│   ├── suppressions.json          # Liste noire des sons supprimés
│   └── profils/                   # Profils artistes (bio, photos)
├── musiques/                      # MP3 + fichiers .lrc téléchargés
├── supprimer_filigrane.py         # Script suppression filigrane OpenCV
└── requirements.txt
```
