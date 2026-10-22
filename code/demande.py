"""Niveau de demande d'un set (forte / moyenne / faible), en croisant des
signaux indépendants, pour ne pas acheter ce qui ne se revend pas :
- Brickset : nombre de collectionneurs qui veulent le set, rang parmi les sets de la même année ;
- Dealabs : température des deals du set, sinon de sa gamme (intérêt des acheteurs français) ;
- historique de ventes privé (si fourni) : écoulement et délai par gamme, seulement
  quand la gamme a assez de sets pour juger ;
- gammes signalées difficiles à revendre (paramètre privé GAMMES_DIFFICILES).
Écrit demande et demande_detail dans ../data/referentiel.csv.

Usage : python3 demande.py [<fichier des ventes>.xlsx]
"""
import gzip
import json
import sys

import pandas as pd

import regles
from referentiel import CACHE, DATA

MIN_SETS_GAMME = 10


def brickset_wanted() -> pd.Series:
    lignes = []
    for f in sorted(CACHE.glob("brickset_20*.json.gz")):
        for x in json.load(gzip.open(f)):
            c = x.get("collections") or {}
            lignes.append((x["number"], x["year"], c.get("wantedBy")))
    d = pd.DataFrame(lignes, columns=["base", "year", "wanted"]).drop_duplicates("base", keep="last")
    return d.set_index("base").wanted.groupby(d.set_index("base").year).rank(pct=True)


def rotation(xlsx: str) -> pd.DataFrame:
    """Par gamme : sets achetés, part écoulée, délai médian de vente (jours)."""
    inv = pd.read_excel(xlsx, sheet_name=0)
    ventes = pd.read_excel(xlsx, sheet_name=1).iloc[:, :28]
    inv["Gamme"] = inv.Gamme.astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    s = inv[(inv.Categorie == "SET") & (inv["Strategie vente"].astype(str).str.strip() == "ENTIER")].copy()
    vs = ventes[ventes.Produit == "SET"][["ID Achat", "Date Vente"]].merge(
        s[["ID", "Gamme", "Date achat"]], left_on="ID Achat", right_on="ID")
    vs["jours"] = (pd.to_datetime(vs["Date Vente"]) - pd.to_datetime(vs["Date achat"])).dt.days
    s["vendus"] = s.ID.map(vs.groupby("ID").size()).fillna(0).clip(upper=s.Quantite)
    g = s.groupby("Gamme").agg(achetes=("Quantite", "sum"), vendus=("vendus", "sum"))
    g["ecoule"] = g.vendus / g.achetes
    g["j_med"] = vs.groupby("Gamme").jours.median()
    return g[g.achetes >= MIN_SETS_GAMME]


def niveau(wp, temp_set, chaleur, rot, difficile) -> tuple[str, str]:
    pts, raisons = 0, []
    if wp == wp and wp is not None:
        pts += 2 if wp >= 0.8 else 1 if wp >= 0.6 else -1 if wp < 0.4 else 0
        raisons.append(f"Brickset top {100 - wp * 100:.0f} %")
    if temp_set == temp_set and temp_set is not None:
        pts += 1 if temp_set >= 200 else 0
        raisons.append(f"deal Dealabs à {temp_set:.0f}°")
    elif chaleur == chaleur and chaleur is not None:
        pts += 1 if chaleur >= 150 else -1 if chaleur < 70 else 0
        raisons.append(f"gamme à {chaleur:.0f}° sur Dealabs")
    if rot is not None:
        vite = rot.ecoule >= 0.6 and rot.j_med == rot.j_med and rot.j_med <= 120
        lent = rot.ecoule < 0.3
        pts += 1 if vite else -1 if lent else 0
        raisons.append(f"tes ventes : {rot.ecoule:.0%} écoulé" + (f" en {rot.j_med:.0f} j" if rot.j_med == rot.j_med else ""))
    if difficile:
        pts -= 3
        raisons.append("gamme difficile à revendre")
    return ("forte" if pts >= 2 else "moyenne" if pts >= 0 else "faible"), ", ".join(raisons)


def construire(xlsx: str | None = None):
    ref = pd.read_csv(DATA / "referentiel.csv", dtype={"base": str, "set_num": str}, low_memory=False)
    wp = brickset_wanted()
    temp = pd.read_csv(DATA / "dealabs_deals.csv", dtype={"set": str}).groupby("set").temperature.max()
    rot = rotation(xlsx) if xlsx else None
    if rot is not None:
        rot.to_csv(DATA / "rotation_gammes.csv")
    elif (DATA / "rotation_gammes.csv").exists():
        rot = pd.read_csv(DATA / "rotation_gammes.csv", index_col=0)
    diff = [g.lower() for g in getattr(regles, "GAMMES_DIFFICILES", [])]
    res = [niveau(wp.get(b), temp.get(b), ch,
                  rot.loc[g] if rot is not None and g in rot.index else None,
                  str(t).lower() in diff or str(g).lower() in diff)
           for b, ch, g, t in zip(ref.base, ref.chaleur_dealabs, ref.gamme, ref.theme_bs)]
    ref["demande"], ref["demande_detail"] = zip(*res)
    ref.to_csv(DATA / "referentiel.csv", index=False)
    print(ref[ref.statut == "en vente"].demande.value_counts().to_dict())


if __name__ == "__main__":
    construire(sys.argv[1] if len(sys.argv) > 1 else None)
