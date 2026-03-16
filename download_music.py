#!/usr/bin/env python3
"""
Téléchargeur de musique Sonauto.ai
Télécharge les sons likés avec leurs lyrics synchronisés au format MP3.

Usage:
  # Télécharger tous les sons likés
  python3 download_music.py --tous

  # Télécharger un seul son par son ID
  python3 download_music.py --id <generation_id>

  # Lister les sons likés sans télécharger
  python3 download_music.py --lister

Comment obtenir votre token :
  1. Ouvrir https://sonauto.ai dans votre navigateur
  2. Se connecter à votre compte
  3. Ouvrir DevTools (F12) > Onglet "Network" (Réseau)
  4. Actualiser la page ou cliquer sur une chanson
  5. Chercher une requête vers "sonauto.ai" ou "clerk.sonauto.ai"
  6. Dans les en-têtes de requête, copier la valeur de "Authorization: Bearer ..."
  OU
  7. DevTools > Application > Cookies > sonauto.ai
  8. Copier la valeur du cookie "__session" ou "token"
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from tqdm import tqdm

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1, USLT, SYLT, Encoding, APIC
    MUTAGEN_OK = True
except ImportError:
    MUTAGEN_OK = False
    print("[Attention] mutagen non disponible - les métadonnées ne seront pas intégrées")


# ─── Configuration ────────────────────────────────────────────────────────────

DOSSIER_SORTIE = Path("musiques")
FICHIER_CONFIG = Path(".config_sonauto.json")

API_BASE      = "https://sonauto.ai"
API_LIKED     = f"{API_BASE}/api/liked-songs"
API_GENERATION = f"{API_BASE}/api/generations"


# ─── Gestion du token ─────────────────────────────────────────────────────────

def charger_config() -> dict:
    if FICHIER_CONFIG.exists():
        with open(FICHIER_CONFIG) as f:
            return json.load(f)
    return {}

def sauvegarder_config(config: dict):
    with open(FICHIER_CONFIG, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(FICHIER_CONFIG, 0o600)

def obtenir_token() -> str:
    config = charger_config()
    if config.get("token"):
        return config["token"]

    print("\n─── Configuration du token ─────────────────────────────────────")
    print("Comment obtenir votre token Sonauto.ai :")
    print("  1. Ouvrir https://sonauto.ai et se connecter")
    print("  2. F12 > Réseau (Network) > actualiser la page")
    print("  3. Chercher une requête vers l'API (ex: /api/liked-songs)")
    print("  4. Dans les en-têtes, copier : Authorization: Bearer <token>")
    print("─────────────────────────────────────────────────────────────────\n")
    token = input("Collez votre token ici : ").strip()

    if token.startswith("Bearer "):
        token = token[7:]

    config["token"] = token
    sauvegarder_config(config)
    print("Token sauvegardé dans .config_sonauto.json\n")
    return token


# ─── Client API ───────────────────────────────────────────────────────────────

class ClientSonauto:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Referer": "https://sonauto.ai/",
            "Origin": "https://sonauto.ai",
        })

    def _get(self, url: str, **kwargs) -> requests.Response:
        r = self.session.get(url, **kwargs)
        if r.status_code == 401:
            print("\n[Erreur] Token invalide ou expiré.")
            print("Supprimez .config_sonauto.json et relancez le script.")
            sys.exit(1)
        return r

    def lister_sons_likes(self) -> list[dict]:
        """Récupère tous les sons likés de l'utilisateur."""
        sons = []
        page = 1
        print("Récupération des sons likés...")

        # Essayer plusieurs endpoints possibles
        endpoints = [
            f"{API_BASE}/api/liked-songs",
            f"{API_BASE}/api/v1/liked-songs",
            f"{API_BASE}/api/songs/liked",
            f"{API_BASE}/api/library/liked",
            f"{API_BASE}/api/user/liked-songs",
            f"{API_BASE}/api/generations?liked=true",
        ]

        for endpoint in endpoints:
            try:
                r = self._get(endpoint, params={"page": 1, "limit": 50}, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    print(f"[OK] Endpoint trouvé : {endpoint}")
                    # Normaliser la réponse
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        for cle in ["songs", "items", "generations", "data", "results"]:
                            if cle in data and isinstance(data[cle], list):
                                return data[cle]
                        return [data]
                elif r.status_code not in (404, 405):
                    print(f"[{r.status_code}] {endpoint}")
            except Exception:
                pass

        print("\n[Erreur] Impossible de trouver l'endpoint des sons likés.")
        print("Essayons via l'interface web...")
        return self._lister_via_scraping()

    def _lister_via_scraping(self) -> list[dict]:
        """Fallback : scraper la page liked-songs."""
        r = self._get(f"{API_BASE}/liked-songs", timeout=15)
        if r.status_code != 200:
            print(f"[Erreur] Impossible d'accéder à la page : {r.status_code}")
            return []

        # Chercher les données JSON embarquées dans le HTML (Next.js/SvelteKit)
        patterns = [
            r'window\.__NUXT__\s*=\s*({.*?});',
            r'window\.__NEXT_DATA__\s*=\s*({.*?});',
            r'"liked":\s*(\[.*?\])',
            r'"songs":\s*(\[.*?\])',
        ]
        for pat in patterns:
            m = re.search(pat, r.text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass

        print("[Attention] Données non trouvées dans le HTML.")
        print("Conseil : utilisez l'option --inspecter pour voir les requêtes réseau.")
        return []

    def obtenir_generation(self, generation_id: str) -> dict | None:
        """Récupère les détails d'une génération par son ID."""
        endpoints = [
            f"{API_BASE}/api/generations/{generation_id}",
            f"{API_BASE}/api/v1/generations/{generation_id}",
        ]
        for url in endpoints:
            r = self._get(url, timeout=10)
            if r.status_code == 200:
                return r.json()
        return None

    def telecharger_audio(self, url_audio: str, chemin: Path) -> bool:
        """Télécharge le fichier audio avec une barre de progression."""
        try:
            r = self.session.get(url_audio, stream=True, timeout=60)
            r.raise_for_status()
            taille = int(r.headers.get("content-length", 0))

            with open(chemin, "wb") as f, tqdm(
                total=taille, unit="o", unit_scale=True,
                desc=f"  {chemin.name[:40]}", leave=False
            ) as barre:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    barre.update(len(chunk))
            return True
        except Exception as e:
            print(f"[Erreur téléchargement] {e}")
            return False


# ─── Traitement des lyrics ─────────────────────────────────────────────────────

def extraire_lyrics(son: dict) -> tuple[str, list]:
    """
    Extrait les paroles brutes et les paroles synchronisées.
    Retourne (texte_brut, [(texte, timestamp_ms), ...])
    """
    lyrics_brut = son.get("lyrics") or son.get("prompt") or ""
    lyrics_sync = []

    # Format synchronisé (word-level timing)
    timing = (
        son.get("lyrics_alignment")
        or son.get("alignment")
        or son.get("word_timestamps")
        or []
    )

    if isinstance(timing, list):
        for item in timing:
            if isinstance(item, dict):
                mot   = item.get("word") or item.get("text") or ""
                debut = item.get("start") or item.get("start_time") or 0
                lyrics_sync.append((mot, int(float(debut) * 1000)))

    return lyrics_brut, lyrics_sync

def sauvegarder_lrc(chemin_mp3: Path, lyrics_brut: str, lyrics_sync: list):
    """Sauvegarde les lyrics au format LRC (paroles synchronisées)."""
    chemin_lrc = chemin_mp3.with_suffix(".lrc")

    if lyrics_sync:
        lignes = []
        phrase_courante = []
        dernier_ts = None

        for mot, ts_ms in lyrics_sync:
            secondes = ts_ms // 1000
            centimes = (ts_ms % 1000) // 10
            ts_str = f"[{secondes // 60:02d}:{secondes % 60:02d}.{centimes:02d}]"

            if mot in ("\n", "[Verse", "[Chorus", "[Bridge", "[Outro", "[Intro"):
                if phrase_courante:
                    lignes.append(f"{dernier_ts}{' '.join(phrase_courante)}")
                    phrase_courante = []
                if mot == "\n":
                    lignes.append("")
                else:
                    lignes.append(f"\n{ts_str}{mot}")
            else:
                if dernier_ts is None:
                    dernier_ts = ts_str
                phrase_courante.append(mot)

        if phrase_courante:
            lignes.append(f"{dernier_ts}{' '.join(phrase_courante)}")

        with open(chemin_lrc, "w", encoding="utf-8") as f:
            f.write("\n".join(lignes))

    elif lyrics_brut:
        with open(chemin_lrc, "w", encoding="utf-8") as f:
            f.write(lyrics_brut)

def integrer_metadata_mp3(chemin: Path, son: dict, lyrics_brut: str, lyrics_sync: list):
    """Intègre les métadonnées et lyrics dans le fichier MP3 via ID3."""
    if not MUTAGEN_OK:
        return

    try:
        audio = MP3(chemin)
        tags = audio.tags or ID3()

        # Titre et artiste
        titre = son.get("title") or son.get("name") or "Sans titre"
        tags.add(TIT2(encoding=Encoding.UTF8, text=titre))
        tags.add(TPE1(encoding=Encoding.UTF8, text="Sonauto.ai"))

        # Paroles non synchronisées
        if lyrics_brut:
            tags.add(USLT(
                encoding=Encoding.UTF8,
                lang="fra",
                desc="Paroles",
                text=lyrics_brut
            ))

        # Paroles synchronisées (SYLT)
        if lyrics_sync:
            tags.add(SYLT(
                encoding=Encoding.UTF8,
                lang="fra",
                format=2,  # ms
                type=1,    # paroles
                desc="Paroles synchronisées",
                text=lyrics_sync
            ))

        audio.tags = tags
        audio.save(v2_version=3)

    except Exception as e:
        print(f"  [Attention] Métadonnées non intégrées : {e}")


# ─── Téléchargement ───────────────────────────────────────────────────────────

def nettoyer_nom(nom: str) -> str:
    """Supprime les caractères invalides pour un nom de fichier."""
    nom = re.sub(r'[<>:"/\\|?*]', "", nom)
    nom = nom.strip(". ")
    return nom[:100] or "sans_titre"

def telecharger_son(client: ClientSonauto, son: dict, dossier: Path) -> bool:
    """Télécharge un son avec ses lyrics."""
    # Chercher l'URL audio
    url_audio = (
        son.get("audio_url")
        or son.get("song_path")
        or son.get("mp3_url")
        or son.get("url")
        or (son.get("song_paths") or [None])[0]
    )

    if not url_audio:
        gen_id = son.get("id") or son.get("generation_id") or son.get("task_id")
        if gen_id:
            print(f"  Récupération des détails pour {gen_id}...")
            details = client.obtenir_generation(gen_id)
            if details:
                son.update(details)
                url_audio = (
                    details.get("audio_url")
                    or details.get("song_path")
                    or (details.get("song_paths") or [None])[0]
                )

    if not url_audio:
        print(f"  [Erreur] Aucune URL audio trouvée pour : {son}")
        return False

    # Nom du fichier
    titre = son.get("title") or son.get("name") or son.get("id") or "sans_titre"
    nom_fichier = nettoyer_nom(titre) + ".mp3"
    chemin = dossier / nom_fichier

    # Éviter le re-téléchargement
    if chemin.exists():
        print(f"  [Existe déjà] {nom_fichier}")
        return True

    print(f"\n  Titre    : {titre}")
    print(f"  URL      : {url_audio[:60]}...")

    # Téléchargement
    if not client.telecharger_audio(url_audio, chemin):
        return False

    # Extraction des lyrics
    lyrics_brut, lyrics_sync = extraire_lyrics(son)

    # Sauvegarde .lrc
    if lyrics_brut or lyrics_sync:
        sauvegarder_lrc(chemin, lyrics_brut, lyrics_sync)
        print(f"  Lyrics   : {'synchronisées' if lyrics_sync else 'non synchronisées'} sauvegardées")

    # Intégration dans le MP3
    if chemin.suffix == ".mp3":
        integrer_metadata_mp3(chemin, son, lyrics_brut, lyrics_sync)

    print(f"  Sauvegardé : {chemin}")
    return True


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Téléchargeur de musique Sonauto.ai",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    groupe = parser.add_mutually_exclusive_group(required=True)
    groupe.add_argument("--tous",    action="store_true",
                        help="Télécharger tous les sons likés")
    groupe.add_argument("--id",      metavar="GENERATION_ID",
                        help="Télécharger un seul son par son ID")
    groupe.add_argument("--url",     metavar="URL",
                        help="Télécharger via l'URL de l'éditeur (ex: sonauto.ai/editor/...)")
    groupe.add_argument("--lister",  action="store_true",
                        help="Lister les sons likés sans télécharger")
    groupe.add_argument("--reset-token", action="store_true",
                        help="Réinitialiser le token d'authentification")

    parser.add_argument("--sortie", default="musiques",
                        help="Dossier de sortie (défaut: musiques/)")
    parser.add_argument("--token",  help="Token d'authentification (optionnel)")

    args = parser.parse_args()

    # Reset token
    if args.reset_token:
        FICHIER_CONFIG.unlink(missing_ok=True)
        print("Token supprimé. Relancez le script.")
        return

    # Dossier de sortie
    dossier = Path(args.sortie)
    dossier.mkdir(parents=True, exist_ok=True)

    # Token
    if args.token:
        config = charger_config()
        config["token"] = args.token.replace("Bearer ", "")
        sauvegarder_config(config)

    token = obtenir_token()
    client = ClientSonauto(token)

    # ── Téléchargement via URL ──
    if args.url:
        # Extraire l'ID depuis l'URL : /editor/{song_id}/{generation_id}
        m = re.search(r'/editor/([^/]+)/([^/?]+)', args.url)
        if not m:
            print(f"[Erreur] URL non reconnue : {args.url}")
            sys.exit(1)
        generation_id = m.group(2)
        print(f"ID de génération : {generation_id}")
        son = client.obtenir_generation(generation_id)
        if not son:
            print("[Erreur] Son non trouvé.")
            sys.exit(1)
        telecharger_son(client, son, dossier)

    # ── Téléchargement par ID ──
    elif args.id:
        son = client.obtenir_generation(args.id)
        if not son:
            print(f"[Erreur] Son non trouvé : {args.id}")
            sys.exit(1)
        telecharger_son(client, son, dossier)

    # ── Lister ou tout télécharger ──
    else:
        sons = client.lister_sons_likes()

        if not sons:
            print("[Aucun son trouvé]")
            print("\nConseil : fournissez l'endpoint correct avec --debug")
            sys.exit(1)

        print(f"\n{len(sons)} son(s) liké(s) trouvé(s) :\n")
        for i, son in enumerate(sons, 1):
            titre = son.get("title") or son.get("name") or son.get("id") or "?"
            tags  = son.get("tags") or son.get("style") or ""
            print(f"  {i:3d}. {titre}  [{tags}]")

        if args.lister:
            return

        # Télécharger tout
        print(f"\nTéléchargement dans : {dossier}/\n")
        succes = 0
        for son in sons:
            if telecharger_son(client, son, dossier):
                succes += 1
            time.sleep(0.5)  # Politesse envers le serveur

        print(f"\n── Terminé : {succes}/{len(sons)} son(s) téléchargé(s) ──")
        print(f"   Dossier : {dossier.resolve()}")


if __name__ == "__main__":
    main()
