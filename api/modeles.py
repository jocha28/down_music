from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel


class StatutTelecharge(str, Enum):
    EN_ATTENTE   = "en_attente"
    EN_COURS     = "en_cours"
    TERMINE      = "terminé"
    ERREUR       = "erreur"
    DEJA_PRESENT = "déjà_présent"


class Son(BaseModel):
    id:          str
    titre:       str
    tags:        str  = ""
    paroles:     str  = ""
    url_audio:   str  = ""
    url_editeur: str  = ""
    donnees_brutes: dict[str, Any] = {}


class ProgressionTelechargement(BaseModel):
    id:             str
    titre:          str
    statut:         StatutTelecharge
    progression:    float = 0.0        # 0‑100 %
    octets_recus:   int   = 0
    octets_total:   int   = 0
    chemin_fichier: str   = ""
    erreur:         str   = ""


class ConfigToken(BaseModel):
    token:         str
    refresh_token: str = ""  # optionnel — permet le renouvellement automatique


class ReponseTelechargement(BaseModel):
    tache_id: str
    message:  str
