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
    from mutagen.id3 import ID3, TIT2, TPE1, USLT, SYLT, Encoding
    MUTAGEN_OK = True
except ImportError:
    MUTAGEN_OK = False

from .modeles import ProgressionTelechargement, StatutTelecharge, Son
from .client_sonauto import ClientSonauto


DOSSIER_MUSIQUES = Path("musiques")


# ── Dictionnaire global des tâches ────────────────────────────────────────────

_taches: Dict[str, ProgressionTelechargement] = {}


def obtenir_taches() -> Dict[str, ProgressionTelechargement]:
    return _taches

def obtenir_tache(tache_id: str) -> ProgressionTelechargement | None:
    return _taches.get(tache_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nom_propre(titre: str) -> str:
    import re
    nom = re.sub(r'[<>:"/\\|?*]', "", titre).strip(". ")
    return (nom[:100] or "sans_titre") + ".mp3"


def _extraire_lyrics(son: Son) -> tuple[str, list]:
    brut   = son.paroles
    timing = son.donnees_brutes.get("lyrics_alignment") or []
    synced = [
        (item.get("word",""), int(float(item.get("start",0)) * 1000))
        for item in (timing if isinstance(timing, list) else [])
        if isinstance(item, dict)
    ]
    return brut, synced


def _integrer_id3(chemin: Path, son: Son, brut: str, synced: list):
    if not MUTAGEN_OK:
        return
    try:
        audio = MP3(chemin)
        tags  = audio.tags or ID3()
        tags.add(TIT2(encoding=Encoding.UTF8, text=son.titre))
        tags.add(TPE1(encoding=Encoding.UTF8, text="Sonauto.ai"))
        if brut:
            tags.add(USLT(encoding=Encoding.UTF8, lang="fra", desc="", text=brut))
        if synced:
            tags.add(SYLT(encoding=Encoding.UTF8, lang="fra", format=2, type=1,
                          desc="sync", text=synced))
        audio.tags = tags
        audio.save(v2_version=3)
    except Exception:
        pass


def _sauvegarder_lrc(chemin_mp3: Path, brut: str, synced: list):
    if not brut and not synced:
        return
    chemin_lrc = chemin_mp3.with_suffix(".lrc")
    if synced:
        lignes = []
        for mot, ts_ms in synced:
            s, c = divmod(ts_ms, 1000)
            m, s = divmod(s, 60)
            lignes.append(f"[{m:02d}:{s:02d}.{c//10:02d}]{mot}")
        chemin_lrc.write_text("\n".join(lignes), encoding="utf-8")
    else:
        chemin_lrc.write_text(brut, encoding="utf-8")


# ── Cœur du téléchargement ────────────────────────────────────────────────────

async def _executer_telechargement(tache_id: str, son: Son, token: str):
    tache = _taches[tache_id]
    DOSSIER_MUSIQUES.mkdir(parents=True, exist_ok=True)

    # Si pas d'URL, essayer de la récupérer
    url_audio = son.url_audio
    if not url_audio:
        async with ClientSonauto(token) as client:
            details = await client.obtenir_son(son.id)
            if details:
                url_audio = details.url_audio
                son = details

    if not url_audio:
        tache.statut = StatutTelecharge.ERREUR
        tache.erreur = "Aucune URL audio disponible"
        return

    chemin = DOSSIER_MUSIQUES / _nom_propre(son.titre)

    if chemin.exists():
        tache.statut         = StatutTelecharge.DEJA_PRESENT
        tache.progression    = 100.0
        tache.chemin_fichier = str(chemin)
        return

    tache.statut = StatutTelecharge.EN_COURS

    def maj_progression(recu: int, total: int):
        tache.octets_recus = recu
        tache.octets_total = total
        tache.progression  = round(recu / total * 100, 1) if total else 0

    try:
        async with ClientSonauto(token) as client:
            with open(chemin, "wb") as f:
                async for chunk in client.stream_audio(url_audio, maj_progression):
                    f.write(chunk)

        # Lyrics
        brut, synced = _extraire_lyrics(son)
        _integrer_id3(chemin, son, brut, synced)
        _sauvegarder_lrc(chemin, brut, synced)

        tache.statut         = StatutTelecharge.TERMINE
        tache.progression    = 100.0
        tache.chemin_fichier = str(chemin)

    except Exception as e:
        tache.statut = StatutTelecharge.ERREUR
        tache.erreur = str(e)
        chemin.unlink(missing_ok=True)


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
