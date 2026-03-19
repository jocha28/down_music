"""
Service de téléchargement avec gestion de file d'attente et suivi en temps réel.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Dict

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1, USLT, SYLT, TCON, TDRC, Encoding
    MUTAGEN_OK = True
except ImportError:
    MUTAGEN_OK = False

from .modeles import ProgressionTelechargement, StatutTelecharge, Son
from .client_sonauto import ClientSonauto


DOSSIER_MUSIQUES  = Path("musiques")
FICHIER_SUPPRESSIONS = Path("data/suppressions.json")


# ── Liste noire des sons supprimés ───────────────────────────────────────────

def _charger_suppressions() -> set[str]:
    """Retourne l'ensemble des noms de fichiers supprimés manuellement."""
    import json
    if not FICHIER_SUPPRESSIONS.exists():
        return set()
    try:
        return set(json.loads(FICHIER_SUPPRESSIONS.read_text(encoding="utf-8")))
    except Exception:
        return set()

def _est_supprime(nom_fichier: str) -> bool:
    """Vérifie si un nom de fichier MP3 a été supprimé manuellement."""
    return nom_fichier in _charger_suppressions()


# ── Dictionnaire global des tâches ────────────────────────────────────────────

_taches: Dict[str, ProgressionTelechargement] = {}


def obtenir_taches() -> Dict[str, ProgressionTelechargement]:
    return _taches

def obtenir_tache(tache_id: str) -> ProgressionTelechargement | None:
    return _taches.get(tache_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nom_propre(titre: str, ext: str = ".mp3", dossier: Path | None = None) -> str:
    import re
    nom = re.sub(r'[<>:"/\\|?*]', "", titre).strip(". ")
    base = nom[:100] or "sans_titre"
    if dossier is None:
        return base + ext
    # Éviter les conflits : ajouter (2), (3)… si le fichier existe déjà
    chemin = dossier / (base + ext)
    if not chemin.exists():
        return base + ext
    i = 2
    while (dossier / f"{base} ({i}){ext}").exists():
        i += 1
    return f"{base} ({i}){ext}"


def _generer_lrc(aligned: list) -> str:
    """
    Génère un fichier LRC depuis aligned_lyrics (liste de {start, end, text}).
    Format : [MM:SS.xx]texte
    """
    lignes = []
    for item in aligned:
        if not isinstance(item, dict):
            continue
        start = float(item.get("start", 0))
        texte = str(item.get("text", item.get("word", ""))).strip()
        if not texte:
            continue
        m, s = divmod(start, 60)
        centis = int((start % 1) * 100)
        lignes.append(f"[{int(m):02d}:{int(s):02d}.{centis:02d}]{texte}")
    return "\n".join(lignes)


def _generer_sylt(word_aligned: list) -> list[tuple[str, int]]:
    """
    Construit la liste SYLT (mot, timestamp_ms) pour les tags ID3.
    """
    result = []
    for item in word_aligned:
        if not isinstance(item, dict):
            continue
        mot = str(item.get("word", "")).strip()
        ts_ms = int(float(item.get("start", 0)) * 1000)
        if mot:
            result.append((mot, ts_ms))
    return result


def _integrer_id3(chemin: Path, son: Son, brut: str, lrc: str, sylt: list,
                   genre: str = "", annee: str = ""):
    if not MUTAGEN_OK:
        return
    try:
        audio = MP3(chemin)
        tags  = audio.tags or ID3()
        tags.add(TIT2(encoding=Encoding.UTF8, text=son.titre))
        # Ne pas écraser l'artiste si déjà renseigné manuellement
        if not tags.get("TPE1"):
            tags.add(TPE1(encoding=Encoding.UTF8, text=""))
        if brut:
            tags.add(USLT(encoding=Encoding.UTF8, lang="fra", desc="", text=brut))
        if sylt:
            tags.add(SYLT(encoding=Encoding.UTF8, lang="fra", format=2, type=1,
                          desc="sync", text=sylt))
        if genre:
            tags.add(TCON(encoding=Encoding.UTF8, text=genre))
        if annee:
            tags.add(TDRC(encoding=Encoding.UTF8, text=annee))
        audio.tags = tags
        audio.save(v2_version=3)
    except Exception:
        pass


def _sauvegarder_lrc(chemin_mp3: Path, lrc: str, brut: str):
    contenu = lrc or brut
    if not contenu:
        return
    chemin_mp3.with_suffix(".lrc").write_text(contenu, encoding="utf-8")


# ── Cœur du téléchargement ────────────────────────────────────────────────────

async def _executer_telechargement(tache_id: str, son: Son, token: str):
    tache = _taches[tache_id]
    DOSSIER_MUSIQUES.mkdir(parents=True, exist_ok=True)

    # Vérifier si ce son a été supprimé manuellement → ne pas re-télécharger
    nom_base = _nom_propre(son.titre)
    if _est_supprime(nom_base):
        tache.statut      = StatutTelecharge.DEJA_PRESENT
        tache.progression = 100.0
        tache.erreur      = "supprimé"
        return

    # Nom unique (gère les doublons de titres, ex: "Empire Digital (2).mp3")
    chemin_mp3 = DOSSIER_MUSIQUES / _nom_propre(son.titre, dossier=DOSSIER_MUSIQUES)

    # DEJA_PRESENT uniquement si le fichier avec ce nom exact existe déjà
    if chemin_mp3.exists():
        tache.statut         = StatutTelecharge.DEJA_PRESENT
        tache.progression    = 100.0
        tache.chemin_fichier = str(chemin_mp3)
        return

    tache.statut = StatutTelecharge.EN_COURS

    async with ClientSonauto(token) as client:
        # 1. URL MP3, lyrics et tags en parallèle
        lyrics_id       = son.donnees_brutes.get("lyrics_id") or ""
        track_params_id = son.donnees_brutes.get("track_params_id") or ""
        url_mp3, donnees_lyrics, tags_son = await asyncio.gather(
            client.obtenir_url_mp3(son.id),
            client.obtenir_lyrics(lyrics_id),
            client.obtenir_tags(track_params_id),
        )

    if not url_mp3:
        tache.statut = StatutTelecharge.ERREUR
        tache.erreur = "Impossible d'obtenir l'URL MP3 depuis le backend"
        return

    # Préparer les lyrics
    brut  = ""
    lrc   = ""
    sylt  = []
    if donnees_lyrics:
        brut    = donnees_lyrics.get("lyrics") or ""
        aligned = donnees_lyrics.get("aligned_lyrics") or []
        wals    = donnees_lyrics.get("word_aligned_lyrics") or []
        if aligned:
            lrc  = _generer_lrc(aligned)
        if wals:
            sylt = _generer_sylt(wals)

    # Genre depuis les tags Sonauto
    genre = ", ".join(tags_son) if tags_son else ""

    # Année depuis created_at  (ex: "2026-03-14T10:07:47.984521+00:00")
    annee = (son.donnees_brutes.get("created_at") or "")[:4]

    def maj_progression(recu: int, total: int):
        tache.octets_recus = recu
        tache.octets_total = total
        tache.progression  = round(recu / total * 95, 1) if total else 0

    chemin_tmp = chemin_mp3.with_suffix(".tmp")
    try:
        # 2. Télécharger le MP3
        async with ClientSonauto(token) as client:
            with open(chemin_tmp, "wb") as f:
                async for chunk in client.stream_audio(url_mp3, maj_progression):
                    f.write(chunk)

        chemin_tmp.rename(chemin_mp3)
        tache.progression = 97.0

        # 3. Tags ID3 + fichier LRC
        _integrer_id3(chemin_mp3, son, brut, lrc, sylt, genre=genre, annee=annee)
        _sauvegarder_lrc(chemin_mp3, lrc, brut)

        tache.statut         = StatutTelecharge.TERMINE
        tache.progression    = 100.0
        tache.chemin_fichier = str(chemin_mp3)

    except Exception as e:
        tache.statut = StatutTelecharge.ERREUR
        tache.erreur = str(e)
        chemin_tmp.unlink(missing_ok=True)


# ── API publique du service ───────────────────────────────────────────────────

def creer_tache(son: Son) -> str:
    tache_id = str(uuid.uuid4())
    _taches[tache_id] = ProgressionTelechargement(
        id=tache_id,
        titre=son.titre,
        statut=StatutTelecharge.EN_ATTENTE,
    )
    return tache_id


async def lancer_telechargement(son: Son, token: str) -> str:
    tache_id = creer_tache(son)
    asyncio.create_task(_executer_telechargement(tache_id, son, token))
    return tache_id


async def lancer_tous(sons: list[Son], token: str) -> list[str]:
    ids = []
    for son in sons:
        tid = await lancer_telechargement(son, token)
        ids.append(tid)
        await asyncio.sleep(0.3)  # Politesse envers le serveur
    return ids
