"""Liste officielle Lego « Bientôt retirés » (page lego.com copiée-collée par
l'utilisateur, le site bloquant les robots) → ../data/lego_officiel.csv.

Usage : python3 lego_officiel.py fichier.md [fichier2.md ...]
"""
import re
import sys

import pandas as pd

from referentiel import DATA

RE_LIEN = re.compile(r"^\[(.+?)\]\(https://www\.lego\.com/fr-fr/product/[^)]*?-?(\d{4,6})\)$")
RE_PRIX = re.compile(r"(\d+(?:\s?\d{3})*,\d{2})\s*€")


def lire(chemins: list[str]) -> pd.DataFrame:
    lignes = []
    for c in chemins:
        lignes += open(c, encoding="utf-8").read().splitlines()
    sets = {}
    for i, l in enumerate(lignes):
        m = RE_LIEN.match(l.strip())
        if not m:
            continue
        nom, num = m.groups()
        suite = lignes[i + 1:i + 6]
        prix = [float(p.replace(" ", "").replace(" ", "").replace(",", ".")) for p in RE_PRIX.findall(suite[0])] if suite else []
        if not prix:
            continue
        tag = next((s for s in suite if s.startswith("Points") or s in ("Bientôt retiré", "Exclusivités", "En solde")), "")
        dispo = next((s for s in suite if s in ("Ajouter au Panier", "Réassort en cours", "Épuisé", "Rupture de stock",
                                                 "Bientôt disponible", "Temporairement en rupture de stock")), "")
        sets[num] = {"set": num, "nom": nom, "prix_public": prix[0], "prix_actuel": prix[-1],
                     "points_doubles": tag.startswith("Points doublés"),
                     "etiquette": tag.replace("Points doublés", "") or "", "dispo": dispo}
    return pd.DataFrame(sets.values())


if __name__ == "__main__":
    d = lire(sys.argv[1:])
    d.to_csv(DATA / "lego_officiel.csv", index=False)
    print(len(d), "sets ;", d.etiquette.value_counts().to_dict(), d.dispo.value_counts().to_dict())
