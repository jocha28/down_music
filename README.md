# 🎵 Sonauto Downloader

Téléchargez, organisez et éditez tous vos sons likés sur [Sonauto.ai](https://sonauto.ai) depuis une interface web locale.

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

- **Téléchargement automatique** de tous les sons likés (272+ sons, pagination complète Supabase)
- **Tags ID3 automatiques** : titre, genre (depuis Sonauto), année, lyrics synchronisées (SYLT) et non synchronisées (USLT)
- **Interface web** : bibliothèque, suivi des téléchargements en temps réel (SSE), lecteur audio intégré
- **Éditeur de tags** : modifier titre, artiste, album, année, genre, numéro de piste, cover art
- **Gestionnaire d'albums** : regrouper plusieurs sons, réordonner par drag & drop, numéroter les pistes, appliquer une cover à toutes les pistes
- **Viewer LRC** : affichage synchronisé des paroles avec auto-scroll sur la ligne active
- **Suppression de filigrane** : script Python pour effacer le logo Sonauto des covers via inpainting OpenCV

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
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Ouvrir [http://localhost:8000](http://localhost:8000)

---

## Connexion

1. Se connecter sur [sonauto.ai](https://sonauto.ai)
2. Ouvrir la console F12 → onglet **Console**
3. Exécuter :

   ```js
   document.cookie.match(/sb-db-auth-token\.0=([^;]+)/)?.[1]
   ```

4. Coller la valeur (`base64-eyJh...`) dans le champ de connexion de l'interface

Le token est automatiquement renouvelé via le `refresh_token`.

---

## Interface web

| Page | URL | Description |
| --- | --- | --- |
| Bibliothèque | `/` | Sons likés, téléchargement, lecteur + lyrics LRC |
| Éditeur de tags | `/static/editeur.html` | Modifier les tags ID3 d'un MP3 |
| Gestionnaire d'albums | `/static/album.html` | Créer un album, ordonner les pistes |
| Swagger API | `/swagger` | Documentation interactive de l'API |

---

## API — Endpoints principaux

```text
GET  /api/sons                          → lister les sons likés
POST /api/telecharger/{generation_id}  → télécharger un son
POST /api/telecharger/tous             → tout télécharger
GET  /api/taches/{id}/flux             → progression SSE en temps réel
GET  /api/fichiers                     → liste des MP3 locaux
GET  /api/fichiers/{nom}/cover         → cover art (image)
GET  /api/fichiers/{nom}/lyrics        → paroles LRC
GET  /api/fichiers/{nom}/tags          → lire les tags ID3
POST /api/fichiers/{nom}/tags          → écrire les tags ID3
POST /api/tags/synchroniser            → sync genre/année depuis Sonauto
```

---

## Suppression du filigrane

Les covers générées par Sonauto.ai contiennent un petit logo en bas à droite. Pour le supprimer :

```bash
# Tous les MP3 du dossier musiques/
python3 supprimer_filigrane.py

# Un seul fichier MP3
python3 supprimer_filigrane.py "Active.mp3"

# Une image directement (JPG/PNG)
python3 supprimer_filigrane.py cover.jpg
```

Le script détecte les pixels très lumineux dans le coin bas-droite et les reconstitue par inpainting (algorithme TELEA d'OpenCV).

---

## Structure du projet

```text
.
├── main.py                        # Point d'entrée FastAPI
├── api/
│   ├── routes.py                  # Toutes les routes API
│   ├── client_sonauto.py          # Client HTTP Sonauto / Supabase
│   ├── service_telechargement.py  # Logique de téléchargement + ID3
│   └── modeles.py                 # Modèles Pydantic
├── static/
│   ├── index.html                 # Interface principale
│   ├── editeur.html               # Éditeur de tags ID3
│   └── album.html                 # Gestionnaire d'albums
├── musiques/                      # MP3 + fichiers LRC téléchargés
├── supprimer_filigrane.py         # Script suppression filigrane
└── requirements.txt
```
