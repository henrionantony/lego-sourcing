"""Vrai (code 0) si un geste de l'application attend d'être appliqué : le passage
GitHub lance alors le rafraîchissement tout de suite, sans attendre 5 minutes."""
import sys

import base

r = base.lire("app_docs", "select=chemin&chemin=like.actions/*&data->>erreur=is.null&or=(data->>fait.is.null,data->>fait.eq.false)&limit=1")
sys.exit(0 if r else 1)
