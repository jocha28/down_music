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

def _charger_config() -> dict:
    config_path = Path(".config_sonauto.json")
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except Exception:
            pass
    return {}


async def _rafraichir_token(app) -> bool:
    """Renouvelle le token via le refresh_token Supabase si disponible."""
    import httpx
    refresh = getattr(app.state, "refresh_token", None)
    if not refresh:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://db.sonauto.ai/auth/v1/token?grant_type=refresh_token",
                headers={
                    "Content-Type": "application/json",
                    "apikey": "sb_publishable_Ap6tbqA6iU0D4uuvjB5v0A_C0paifNR",
                },
                json={"refresh_token": refresh},
            )
            if r.status_code == 200:
                data = r.json()
                app.state.token         = data["access_token"]
                app.state.refresh_token = data["refresh_token"]
                config = {"token": app.state.token, "refresh_token": app.state.refresh_token}
                config_path = Path(".config_sonauto.json")
                config_path.write_text(json.dumps(config, indent=2))
                config_path.chmod(0o600)
                print("  Token renouvelé automatiquement ✓")
                return True
    except Exception as e:
        print(f"  Échec du renouvellement : {e}")
    return False


@asynccontextmanager
async def duree_de_vie(app: FastAPI):
    import base64, time
    config = _charger_config()
    app.state.token         = config.get("token", "")
    app.state.refresh_token = config.get("refresh_token", "")

    if app.state.token:
        # Vérifier l'expiration du token
        try:
            parts = app.state.token.split(".")
            p = parts[1].replace("-", "+").replace("_", "/")
            p += "=" * (4 - len(p) % 4)
            payload = json.loads(base64.b64decode(p))
            if payload.get("exp", 0) < time.time() + 60:
                print("  Token expiré — renouvellement en cours...")
                await _rafraichir_token(app)
            else:
                print("  Token chargé et valide ✓")
        except Exception:
            print("  Token chargé")
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
