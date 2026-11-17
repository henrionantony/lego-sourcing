"""Frais de port d'un set : poids et taille du colis estimés, puis tarif Colissimo.

Poids : Brickset (poids de la boîte) quand il le donne, sinon volume de la boîte × densité
moyenne des sets (0,112 kg/dm³, erreur médiane ~17 %), sinon nombre de pièces (~37 %).
Colis = boîte + 3 cm par côté, + carton et calage. Tarifs publics La Poste 2026 (guichet).
Le port demandé à l'acheteur = tarif de la tranche + emballage + imprévu, arrondi au 0,50 € au-dessus :
on ne perd jamais sur le port, même quand le poids est estimé un peu court (marge de 10 %).
"""
import glob
import gzip
import json
import math
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"

# (poids max kg, prix €) — Colissimo 2026, prix publics
FRANCE = [(0.25, 5.49), (0.5, 7.59), (0.75, 9.29), (1, 9.59), (2, 11.19), (5, 17.39), (10, 25.29)]
EUROPE = [(0.5, 14.99), (1, 19.39), (2, 22.19), (5, 28.59), (10, 46.99), (15, 67.99), (30, 87.99)]  # UE + Suisse
SUPPLEMENT_UK = 4.0
EMBALLAGE = 1.0          # carton, papier bulle, scotch
IMPREVU = 2.0            # marge pour un colis plus lourd ou plus cher que prévu
MARGE_POIDS = 1.10       # poids estimé (pas mesuré) : on se garde 10 %
DENSITE = 0.112          # kg par dm³ de boîte
CARTON_KG = 0.15
COTE_MAX_CM, SOMME_MAX_CM = 100, 150   # au-delà : colis hors norme, prix à vérifier au guichet

_BS = None


def _brickset() -> dict:
    global _BS
    if _BS is None:
        _BS = {}
        for f in glob.glob(str(CACHE / "brickset_*.json.gz")):
            for s in json.load(gzip.open(f)):
                _BS[str(s.get("number"))] = {"d": s.get("dimensions") or {}, "p": s.get("pieces")}
    return _BS


def colis(s: str, pieces=None) -> dict | None:
    """{"kg": poids colis, "dims": [cm…] ou None, "src": brickset|volume|pieces, "hors_norme": bool}"""
    b = _brickset().get(str(s), {})
    d, p = b.get("d") or {}, b.get("p") or pieces
    dims = [d.get(k) for k in ("height", "width", "depth")]
    dims = [x for x in dims if x] if all(dims) else None
    if d.get("weight"):
        kg, src = float(d["weight"]), "brickset"
    elif dims:
        kg, src = dims[0] * dims[1] * dims[2] / 1000 * DENSITE * MARGE_POIDS, "volume"
    elif p:
        kg, src = (0.0014 * float(p) + 0.13) * MARGE_POIDS, "pieces"
    else:
        return None
    if dims:
        dims = sorted((round(x + 3) for x in dims), reverse=True)
    kg = kg + CARTON_KG + (0.05 * kg if src == "brickset" else 0)
    hors = bool(dims and (dims[0] > COTE_MAX_CM or sum(dims) > SOMME_MAX_CM))
    return {"kg": round(kg, 2), "dims": dims, "src": src, "hors_norme": hors}


def tarif(grille, kg) -> float | None:
    for m, p in grille:
        if kg <= m:
            return p
    return None


def demande(cout) -> float | None:
    return None if cout is None else math.ceil((cout + EMBALLAGE + IMPREVU) * 2) / 2


def port(s: str, pieces=None) -> dict | None:
    """Frais de port à demander : France, UE + Suisse, Royaume-Uni (None si hors grille)."""
    c = colis(s, pieces)
    if not c:
        return None
    ue = tarif(EUROPE, c["kg"])
    return {**c, "fr": demande(tarif(FRANCE, c["kg"])), "ue": demande(ue),
            "uk": demande(ue + SUPPLEMENT_UK if ue else None)}


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:]:
        print(s, port(s))
