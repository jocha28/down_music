#!/usr/bin/env python3
"""
Point d'entrée de l'API FastAPI — Téléchargeur Sonauto.ai
Lancer : python3 main.py  (ou uvicorn main:app --reload)
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router


# ── Chargement du token au démarrage ─────────────────────────────────────────

def _charger_token_sauvegarde() -> str:
    config_path = Path(".config_sonauto.json")
    if config_path.exists():
        try:
            return json.loads(config_path.read_text()).get("token", "")
        except Exception:
            pass
    return ""


@asynccontextmanager
async def duree_de_vie(app: FastAPI):
    app.state.token = _charger_token_sauvegarde()
    if app.state.token:
        print(f"  Token chargé depuis .config_sonauto.json")
    else:
        print("  Aucun token — POST /config/token pour en définir un")
    yield


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Téléchargeur Sonauto.ai",
    description="""
API pour télécharger vos sons likés sur **Sonauto.ai** avec leurs lyrics synchronisées.

## Démarrage rapide

1. **Configurer votre token** → `POST /config/token`
2. **Lister vos sons** → `GET /sons`
3. **Tout télécharger** → `POST /telecharger/tous`
4. **Suivre la progression** → `GET /taches`

## Comment obtenir le token

1. Ouvrir https://sonauto.ai et se connecter
2. F12 → Réseau → actualiser la page
3. Trouver une requête vers `/api/...`
4. Copier `Authorization: Bearer <token>`
    """,
    version="1.0.0",
    lifespan=duree_de_vie,
    docs_url="/",
    redoc_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


# ── Lancement direct ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["."],
    )
