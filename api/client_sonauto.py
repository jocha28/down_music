"""
Client HTTP pour l'API Sonauto.ai.
Gère l'authentification et la récupération des données.
"""
from __future__ import annotations
import re
import json
from typing import AsyncGenerator, Callable

import httpx

from .modeles import Son


HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer":    "https://sonauto.ai/",
    "Origin":     "https://sonauto.ai",
}

ENDPOINTS_LIKES = [
    "https://sonauto.ai/api/liked-songs",
    "https://sonauto.ai/api/v1/liked-songs",
    "https://sonauto.ai/api/songs/liked",
    "https://sonauto.ai/api/library/liked",
    "https://sonauto.ai/api/user/liked-songs",
    "https://sonauto.ai/api/generations?liked=true",
]

ENDPOINTS_GENERATION = [
    "https://sonauto.ai/api/generations/{id}",
    "https://sonauto.ai/api/v1/generations/{id}",
]


def _normaliser_son(data: dict) -> Son:
    """Convertit un dict brut de l'API en objet Son."""
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
    url_audio = (
        data.get("audio_url")
        or data.get("song_path")
        or data.get("mp3_url")
        or data.get("url")
        or (data.get("song_paths") or [None])[0]
        or ""
    )
    return Son(
        id=gen_id,
        titre=titre,
        tags=str(data.get("tags") or data.get("style") or ""),
        paroles=str(data.get("lyrics") or data.get("prompt") or ""),
        url_audio=url_audio,
        url_editeur=f"https://sonauto.ai/editor/{gen_id}",
        donnees_brutes=data,
    )


class ClientSonauto:
    def __init__(self, token: str):
        self._token = token
        self._client = httpx.AsyncClient(
            headers={
                **HEADERS_BASE,
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
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

    # ── Sons likés ────────────────────────────────────────────────────────────

    async def lister_sons_likes(self) -> list[Son]:
        for endpoint in ENDPOINTS_LIKES:
            try:
                r = await self._client.get(endpoint, params={"page": 1, "limit": 200})
                if r.status_code == 401:
                    raise PermissionError("Token invalide ou expiré")
                if r.status_code == 200:
                    data = r.json()
                    raw_list = (
                        data if isinstance(data, list)
                        else next(
                            (data[k] for k in ("songs","items","generations","data","results")
                             if isinstance(data.get(k), list)),
                            None
                        )
                    )
                    if raw_list is not None:
                        return [_normaliser_son(s) for s in raw_list]
            except PermissionError:
                raise
            except Exception:
                pass

        # Fallback : scraping HTML
        return await self._lister_via_html()

    async def _lister_via_html(self) -> list[Son]:
        r = await self._client.get("https://sonauto.ai/liked-songs")
        if r.status_code != 200:
            return []
        for pattern in [
            r'window\.__NUXT__\s*=\s*({.*?});',
            r'window\.__NEXT_DATA__\s*=\s*({.*?});',
            r'"liked":\s*(\[.*?\])',
            r'"songs":\s*(\[.*?\])',
        ]:
            m = re.search(pattern, r.text, re.DOTALL)
            if m:
                try:
                    items = json.loads(m.group(1))
                    if isinstance(items, list):
                        return [_normaliser_son(s) for s in items]
                except json.JSONDecodeError:
                    pass
        return []

    # ── Génération unique ─────────────────────────────────────────────────────

    async def obtenir_son(self, generation_id: str) -> Son | None:
        for tmpl in ENDPOINTS_GENERATION:
            url = tmpl.format(id=generation_id)
            try:
                r = await self._client.get(url)
                if r.status_code == 200:
                    return _normaliser_son(r.json())
                if r.status_code == 401:
                    raise PermissionError("Token invalide ou expiré")
            except PermissionError:
                raise
            except Exception:
                pass
        return None

    # ── Téléchargement streamé ────────────────────────────────────────────────

    async def stream_audio(
        self,
        url: str,
        callback_progression: Callable[[int, int], None] | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Générateur asynchrone de chunks audio avec callback de progression."""
        async with self._client.stream("GET", url, timeout=120) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            recu  = 0
            async for chunk in r.aiter_bytes(chunk_size=16_384):
                recu += len(chunk)
                if callback_progression:
                    callback_progression(recu, total)
                yield chunk
