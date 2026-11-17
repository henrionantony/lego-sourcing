"""Argent que le compte LEGO doit au compte perso (dépense pro payée par le perso,
ancienne cagnotte perso utilisée pour une commande pro...). Déjà compté dans la
trésorerie : le virement pro → perso ne change pas la caisse. Fichier privé."""
import datetime as dt
from pathlib import Path

import base

FICHIER = Path(__file__).resolve().parent.parent / "stock" / "dus_perso.json"


def lire() -> list[dict]:
    return base.json_partage("dus_perso", FICHIER, [])


def ouverts() -> list[dict]:
    return [d for d in lire() if not d.get("regle_le")]


def ajouter(d: str, montant: float, titre: str, sous: str = "") -> str:
    """Nouvelle dette du compte LEGO envers le perso ; renvoie son id."""
    tout = lire()
    n = 1 + max([int(x["id"].split("-")[-1]) for x in tout if x["id"].split("-")[-1].isdigit()] or [0])
    i = f"duperso-{n}"
    tout.append({"id": i, "d": d, "montant": round(float(montant), 2), "titre": titre, "sous": sous, "regle_le": None})
    base.json_partage_ecrire("dus_perso", FICHIER, tout)
    return i


def regler(i: str, le: str | None = None) -> bool:
    tout = lire()
    for x in tout:
        if x["id"] == i and not x.get("regle_le"):
            x["regle_le"] = (le or dt.date.today().isoformat())[:10]
            base.json_partage_ecrire("dus_perso", FICHIER, tout)
            return True
    return False
