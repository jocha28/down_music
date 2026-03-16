"""
Client HTTP pour l'API Sonauto.ai.

Architecture découverte via DevTools :
  - Auth        : db.sonauto.ai/auth/v1/token        (Supabase Auth)
  - REST        : db.sonauto.ai/rest/v1/tracks        (Supabase PostgREST)
  - Backend     : p.sonauto.ai/process/download_audio (API backend privée)
  - CDN MP3     : cdn.sonauto.ai/generations3_altformats/audio_{id}_N.mp3
  - CDN OGG     : scdn.sonauto.ai/generations3/audio_{id}_N.ogg
  - Refresh     : db.sonauto.ai/auth/v1/token?grant_type=refresh_token
"""
from __future__ import annotations

import re
import json
import subprocess
from pathlib import Path
from typing import AsyncGenerator, Callable

import httpx

from .modeles import Son


# ── URLs de base ──────────────────────────────────────────────────────────────

SUPABASE_URL      = "https://db.sonauto.ai"
CDN_BASE          = "https://cdn.sonauto.ai"
CDN_OGG_BASE      = "https://scdn.sonauto.ai"
API_BASE          = "https://sonauto.ai"
BACKEND_BASE      = "https://p.sonauto.ai"

# Clé publishable Supabase — récupérée depuis 5570-f1abfd8e94cccc95.js
SUPABASE_ANON_KEY = "sb_publishable_Ap6tbqA6iU0D4uuvjB5v0A_C0paifNR"

HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer":         f"{API_BASE}/",
    "Origin":          API_BASE,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normaliser_son(data: dict) -> Son:
    """Convertit un dict brut de la table 'tracks' en objet Son."""
    track_id    = data.get("id") or "inconnu"
    gen_id      = data.get("generation_id") or track_id
    titre       = data.get("title") or gen_id[:8]
    song_path   = data.get("song_path") or ""

    # URL audio : construite depuis song_path sur le CDN
    url_audio = f"{CDN_OGG_BASE}/{song_path}" if song_path else ""

    return Son(
        id=track_id,
        titre=titre,
        tags=str(data.get("tags") or ""),
        paroles=str(data.get("lyrics") or ""),
        url_audio=url_audio,
        url_editeur=f"{API_BASE}/editor/{gen_id}",
        donnees_brutes=data,
    )


# ── Conversion OGG → MP3 ──────────────────────────────────────────────────────

def convertir_ogg_en_mp3(chemin_ogg: Path) -> Path:
    """Convertit un fichier OGG/Opus en MP3 via ffmpeg."""
    chemin_mp3 = chemin_ogg.with_suffix(".mp3")
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(chemin_ogg),
            "-codec:a", "libmp3lame", "-q:a", "2",
            str(chemin_mp3)
        ],
        check=True
    )
    chemin_ogg.unlink()
    return chemin_mp3


# ── Client principal ──────────────────────────────────────────────────────────

class ClientSonauto:
    def __init__(self, token: str):
        self._token    = token
        self._client   = httpx.AsyncClient(
            headers={
                **HEADERS_BASE,
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            timeout=30,
            follow_redirects=True,
        )

    async def fermer(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.fermer()

    # ── Helpers Supabase ──────────────────────────────────────────────────────

    @property
    def _headers_supa(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "apikey":        SUPABASE_ANON_KEY,
            "Prefer":        "return=representation",
        }

    # ── Sons likés via Supabase PostgREST ─────────────────────────────────────

    async def lister_sons_likes(self) -> list[Son]:
        """Récupère TOUS les sons likés en paginant.

        Supabase plafonne les réponses côté serveur (≈60 par page).
        On continue jusqu'à atteindre le total indiqué par Content-Range,
        ou jusqu'à recevoir une page vide.
        """
        PAGE = 60   # correspond au plafond Supabase observé
        tous: list[dict] = []
        offset = 0
        total_supabase = None
        try:
            while True:
                r = await self._client.get(
                    f"{SUPABASE_URL}/rest/v1/tracks",
                    params={
                        "select":   "id,title,song_path,generation_id,lyrics_id,favorite,deleted",
                        "favorite": "eq.true",
                        "deleted":  "eq.false",
                        "order":    "created_at.desc",
                        "limit":    str(PAGE),
                        "offset":   str(offset),
                    },
                    headers={**self._headers_supa, "Prefer": "count=exact"},
                    timeout=20,
                )
                if r.status_code == 401:
                    raise PermissionError("Token invalide ou expiré")
                if r.status_code not in (200, 206):
                    break
                # Lire le total depuis Content-Range (ex: "0-59/272")
                if total_supabase is None:
                    cr = r.headers.get("content-range", "")
                    if "/" in cr:
                        total_supabase = int(cr.split("/")[-1])
                page = r.json()
                if not isinstance(page, list) or not page:
                    break
                tous.extend(page)
                offset += len(page)
                if total_supabase is not None and len(tous) >= total_supabase:
                    break
        except PermissionError:
            raise
        except Exception:
            pass
        return [_normaliser_son(s) for s in tous]

    # ── Lyrics ────────────────────────────────────────────────────────────────

    async def obtenir_lyrics(self, lyrics_id: str) -> dict | None:
        """
        Récupère les lyrics depuis la table 'lyrics' par ID.
        Retourne un dict avec : lyrics (str), aligned_lyrics (list), word_aligned_lyrics (list)
        """
        if not lyrics_id:
            return None
        try:
            r = await self._client.get(
                f"{SUPABASE_URL}/rest/v1/lyrics",
                params={"id": f"eq.{lyrics_id}", "select": "lyrics,aligned_lyrics,word_aligned_lyrics"},
                headers=self._headers_supa,
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    return data[0]
        except Exception:
            pass
        return None

    # ── Obtenir l'URL MP3 via le backend ──────────────────────────────────────

    async def obtenir_url_mp3(self, track_id: str) -> str | None:
        """
        Demande au backend l'URL MP3 pour un track donné.
        Endpoint découvert dans le chunk JS 5570 : POST p.sonauto.ai/process/download_audio
        Retourne directement une URL CDN publique cdn.sonauto.ai/…/….mp3
        """
        try:
            r = await self._client.post(
                f"{BACKEND_BASE}/process/download_audio",
                json={"track_id": track_id, "format": "mp3"},
                timeout=60,
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("audio_url") or data.get("url") or ""
        except Exception:
            pass
        return None

    # ── Son unique ────────────────────────────────────────────────────────────

    async def obtenir_son(self, track_id: str) -> Son | None:
        """Récupère un son par son ID de track."""
        headers_supa = {
            "Authorization": f"Bearer {self._token}",
            "apikey":        SUPABASE_ANON_KEY,
        }
        try:
            r = await self._client.get(
                f"{SUPABASE_URL}/rest/v1/tracks",
                params={"id": f"eq.{track_id}", "select": "*"},
                headers=headers_supa,
                timeout=15,
            )
            if r.status_code == 401:
                raise PermissionError("Token invalide ou expiré")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    return _normaliser_son(data[0])
        except PermissionError:
            raise
        except Exception:
            pass
        return None

    # ── Résolution URL audio (fallback OGG CDN) ───────────────────────────────

    async def resoudre_url_audio(self, track_id: str) -> str | None:
        """Résout l'URL audio via le backend (MP3 direct)."""
        return await self.obtenir_url_mp3(track_id)

    # ── Téléchargement streamé ────────────────────────────────────────────────

    async def stream_audio(
        self,
        url: str,
        callback_progression: Callable[[int, int], None] | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Générateur asynchrone de chunks audio avec suivi de progression."""
        # Les URLs CDN cdn.sonauto.ai sont publiques — pas besoin d'auth
        client = httpx.AsyncClient(timeout=300, follow_redirects=True)
        async with client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                recu  = 0
                async for chunk in r.aiter_bytes(chunk_size=16_384):
                    recu += len(chunk)
                    if callback_progression:
                        callback_progression(recu, total)
                    yield chunk
