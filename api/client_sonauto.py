"""
Client HTTP pour l'API Sonauto.ai.

Architecture découverte via DevTools :
  - Auth    : db.sonauto.ai/auth/v1/token  (Supabase)
  - REST    : db.sonauto.ai/rest/v1/       (Supabase PostgREST)
  - CDN     : scdn.sonauto.ai              (audio OGG + images)
  - Audio   : audio_{id}_0.ogg             (Opus dans conteneur OGG)
  - Next.js : liked-songs?_rsc=...         (React Server Components)
"""
from __future__ import annotations

import re
import json
import subprocess
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Callable

import httpx

from .modeles import Son


# ── URLs de base ──────────────────────────────────────────────────────────────

SUPABASE_URL  = "https://db.sonauto.ai"
CDN_BASE      = "https://scdn.sonauto.ai"
API_BASE      = "https://sonauto.ai"

# Clé publique Supabase (anon key) — nécessaire pour les requêtes REST
# Récupérée depuis les chunks Next.js de sonauto.ai
SUPABASE_ANON_KEY = ""  # sera déduite dynamiquement si vide

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

def _url_audio_cdn(audio_id: str) -> str:
    """Construit l'URL CDN d'un fichier audio (format OGG découvert via DevTools)."""
    return f"{CDN_BASE}/audio_{audio_id}_0.ogg"


def _normaliser_son(data: dict) -> Son:
    """Convertit un dict brut Supabase/API en objet Son."""
    # L'ID principal est celui de la génération
    gen_id = (
        data.get("id")
        or data.get("generation_id")
        or data.get("task_id")
        or "inconnu"
    )
    titre = (
        data.get("title")
        or data.get("name")
        or gen_id[:8]
    )

    # URL audio : plusieurs champs possibles + construction CDN
    audio_id  = data.get("audio_id") or data.get("audio_uuid") or ""
    url_audio = (
        data.get("audio_url")
        or data.get("song_path")
        or data.get("mp3_url")
        or data.get("file_url")
        or (data.get("song_paths") or [None])[0]
        or (data.get("audio_paths") or [None])[0]
        or (_url_audio_cdn(audio_id) if audio_id else "")
        or (_url_audio_cdn(gen_id))   # fallback : l'ID de génération = ID audio
        or ""
    )

    # Normaliser les URLs relatives
    if url_audio and url_audio.startswith("/"):
        url_audio = f"{CDN_BASE}{url_audio}"

    return Son(
        id=gen_id,
        titre=titre,
        tags=str(data.get("tags") or data.get("style") or ""),
        paroles=str(data.get("lyrics") or data.get("prompt") or ""),
        url_audio=url_audio,
        url_editeur=f"{API_BASE}/editor/{gen_id}",
        donnees_brutes=data,
    )


# ── Extraction __NEXT_DATA__ ──────────────────────────────────────────────────

def _extraire_next_data(html: str) -> list[dict] | None:
    """Parse le JSON __NEXT_DATA__ embarqué par Next.js dans le HTML."""
    # Balise script avec id="__NEXT_DATA__"
    m = re.search(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*({.*?})\s*</script>',
        html, re.DOTALL
    )
    if not m:
        m = re.search(r'window\.__NEXT_DATA__\s*=\s*({.*?});\s*</script>', html, re.DOTALL)
    if m:
        try:
            return _chercher_sons(_json_safe(m.group(1)))
        except Exception:
            pass

    # Patterns inline dans les props RSC
    for pat in [
        r'"likedSongs"\s*:\s*(\[.*?\])',
        r'"liked_songs"\s*:\s*(\[.*?\])',
        r'"tracks"\s*:\s*(\[.*?\])',
        r'"songs"\s*:\s*(\[.*?\])',
        r'"generations"\s*:\s*(\[.*?\])',
    ]:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                items = json.loads(m.group(1))
                if _est_liste_sons(items):
                    return items
            except Exception:
                pass
    return None


def _json_safe(s: str) -> dict:
    """Décode JSON en ignorant les erreurs de troncature."""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Tenter de fermer les structures ouvertes
        for suffix in ["}", "}}", "}}}}", "]}", "]}}"]:
            try:
                return json.loads(s + suffix)
            except Exception:
                pass
        return {}


def _est_liste_sons(items: list) -> bool:
    return (
        isinstance(items, list)
        and bool(items)
        and isinstance(items[0], dict)
        and any(k in items[0] for k in ("id", "generation_id", "audio_url", "song_path"))
    )


def _chercher_sons(obj, profondeur: int = 0) -> list[dict] | None:
    if profondeur > 7:
        return None
    CLES = {"likedSongs","liked_songs","tracks","songs","generations","items","data","results"}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in CLES and _est_liste_sons(v):
                return v
        for v in obj.values():
            r = _chercher_sons(v, profondeur + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _chercher_sons(item, profondeur + 1)
            if r:
                return r
    return None


# ── Conversion OGG → MP3 ──────────────────────────────────────────────────────

def convertir_ogg_en_mp3(chemin_ogg: Path) -> Path:
    """Convertit un fichier OGG/Opus en MP3 via ffmpeg."""
    chemin_mp3 = chemin_ogg.with_suffix(".mp3")
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(chemin_ogg),
            "-codec:a", "libmp3lame", "-q:a", "2",  # VBR qualité haute
            str(chemin_mp3)
        ],
        check=True
    )
    chemin_ogg.unlink()
    return chemin_mp3


# ── Client principal ──────────────────────────────────────────────────────────

class ClientSonauto:
    def __init__(self, token: str):
        self._token   = token
        self._anon_key = SUPABASE_ANON_KEY
        self._client  = httpx.AsyncClient(
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

    # ── Récupération de la clé anon Supabase ─────────────────────────────────

    async def _obtenir_anon_key(self) -> str:
        """Extrait la clé anon Supabase depuis les chunks JS de sonauto.ai."""
        if self._anon_key:
            return self._anon_key
        try:
            r = await self._client.get(f"{API_BASE}/", timeout=10,
                headers={"Accept": "text/html"})
            # Chercher la clé anon dans le HTML (pattern Supabase: eyJ... longue)
            m = re.search(r'eyJ[A-Za-z0-9_-]{100,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', r.text)
            if m:
                self._anon_key = m.group(0)
                return self._anon_key
        except Exception:
            pass
        return ""

    # ── Sons likés ────────────────────────────────────────────────────────────

    async def lister_sons_likes(self) -> list[Son]:
        """Récupère les sons likés via plusieurs stratégies."""

        # Stratégie 1 : Supabase PostgREST direct
        sons = await self._lister_via_supabase()
        if sons:
            return sons

        # Stratégie 2 : API Next.js standard
        sons = await self._lister_via_api()
        if sons:
            return sons

        # Stratégie 3 : Scraping HTML + __NEXT_DATA__
        return await self._lister_via_html()

    async def _lister_via_supabase(self) -> list[Son]:
        """Requête directe sur la table Supabase 'tracks'."""
        anon_key = await self._obtenir_anon_key()

        # Requêtes PostgREST possibles (ordre = plus précis en premier)
        requetes = [
            # Table tracks avec filtre liked
            (
                f"{SUPABASE_URL}/rest/v1/tracks",
                {"select": "*,generations(*)", "liked": "eq.true", "order": "created_at.desc", "limit": "200"},
            ),
            # Table likes → tracks
            (
                f"{SUPABASE_URL}/rest/v1/likes",
                {"select": "*,tracks(*,generations(*))", "order": "created_at.desc", "limit": "200"},
            ),
            # Table generations (tout)
            (
                f"{SUPABASE_URL}/rest/v1/generations",
                {"select": "*", "order": "created_at.desc", "limit": "200"},
            ),
        ]

        headers_supabase = {
            "Authorization": f"Bearer {self._token}",
            "apikey":        anon_key or self._token,
            "Prefer":        "return=representation",
        }

        for url, params in requetes:
            try:
                r = await self._client.get(url, params=params,
                    headers=headers_supabase, timeout=15)
                if r.status_code == 401:
                    raise PermissionError("Token invalide ou expiré")
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and data:
                        return [_normaliser_son(s) for s in data]
            except PermissionError:
                raise
            except Exception:
                pass
        return []

    async def _lister_via_api(self) -> list[Son]:
        """Essaie les endpoints API classiques de sonauto.ai."""
        endpoints = [
            f"{API_BASE}/api/liked-songs",
            f"{API_BASE}/api/v1/liked-songs",
            f"{API_BASE}/api/songs/liked",
            f"{API_BASE}/api/my-songs?liked=true",
            f"{API_BASE}/api/generations?liked=true&limit=200",
        ]
        for url in endpoints:
            try:
                r = await self._client.get(url, timeout=15)
                if r.status_code == 401:
                    raise PermissionError("Token invalide ou expiré")
                if r.status_code == 200:
                    data = r.json()
                    raw  = (
                        data if isinstance(data, list)
                        else next(
                            (data[k] for k in
                             ("songs","likedSongs","liked_songs","items","generations","data","results")
                             if isinstance(data.get(k), list)),
                            None,
                        )
                    )
                    if raw:
                        return [_normaliser_son(s) for s in raw]
            except PermissionError:
                raise
            except Exception:
                pass
        return []

    async def _lister_via_html(self) -> list[Son]:
        """Scraping HTML + extraction __NEXT_DATA__ (Next.js RSC)."""
        for url in [f"{API_BASE}/liked-songs", f"{API_BASE}/my-music"]:
            try:
                r = await self._client.get(url, timeout=20,
                    headers={"Accept": "text/html,application/xhtml+xml"})
                if r.status_code == 200:
                    items = _extraire_next_data(r.text)
                    if items:
                        return [_normaliser_son(s) for s in items]
            except Exception:
                pass
        return []

    # ── Son unique ────────────────────────────────────────────────────────────

    async def obtenir_son(self, generation_id: str) -> Son | None:
        """Récupère les détails d'un son par ID."""
        # API standard
        for tmpl in [
            f"{API_BASE}/api/generations/{{id}}",
            f"{API_BASE}/api/v1/generations/{{id}}",
            f"{API_BASE}/api/songs/{{id}}",
            f"{SUPABASE_URL}/rest/v1/generations?id=eq.{{id}}&select=*",
        ]:
            url = tmpl.format(id=generation_id)
            try:
                r = await self._client.get(url, timeout=15)
                if r.status_code == 401:
                    raise PermissionError("Token invalide ou expiré")
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and data:
                        data = data[0]
                    if isinstance(data, dict) and data:
                        return _normaliser_son(data)
            except PermissionError:
                raise
            except Exception:
                pass

        # Fallback : page éditeur
        try:
            r = await self._client.get(
                f"{API_BASE}/editor/{generation_id}",
                timeout=20,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            if r.status_code == 200:
                items = _extraire_next_data(r.text)
                if items:
                    return _normaliser_son(items[0])
        except Exception:
            pass
        return None

    # ── Résolution URL audio ──────────────────────────────────────────────────

    async def resoudre_url_audio(self, son_id: str) -> str | None:
        """
        Tente de trouver l'URL audio sur le CDN.
        Format découvert via DevTools : audio_{id}_0.ogg
        """
        candidats = [
            f"{CDN_BASE}/audio_{son_id}_0.ogg",
            f"{CDN_BASE}/audio_{son_id}_0.mp3",
            f"{CDN_BASE}/audio/audio_{son_id}_0.ogg",
            f"{CDN_BASE}/audio/{son_id}.ogg",
            f"{CDN_BASE}/audio/song-{son_id}.ogg",
        ]
        for url in candidats:
            try:
                r = await self._client.head(url, timeout=10)
                if r.status_code in (200, 206):
                    return url
            except Exception:
                pass
        return None

    # ── Téléchargement streamé ────────────────────────────────────────────────

    async def stream_audio(
        self,
        url: str,
        callback_progression: Callable[[int, int], None] | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Générateur asynchrone de chunks audio avec suivi de progression."""
        async with self._client.stream("GET", url, timeout=180) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            recu  = 0
            async for chunk in r.aiter_bytes(chunk_size=16_384):
                recu += len(chunk)
                if callback_progression:
                    callback_progression(recu, total)
                yield chunk
