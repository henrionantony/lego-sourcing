"""Sets qui quittent le catalogue Lego aux deux vagues de l'année (31 juillet
et 31 décembre), d'après les dates de retrait Brickset (rechargées chaque
jour). À acheter en priorité avant la vague, surtout les exclusivités Lego,
souvent épuisées dès le Black Friday.

Usage : python3 liste_retraits.py  → ../black-friday/sets_en_fin_de_vie.xlsx
"""
import datetime as dt

import pandas as pd

from referentiel import DATA, charger
from regles import net

r = charger()
auj = pd.Timestamp(dt.date.today())
# Date de retrait croisée entre les sources (retraits_sources.py), sinon Brickset.
x = pd.read_csv(DATA / "retraits_sources.csv", dtype={"set": str}).set_index("set")
r["rt"] = pd.to_datetime(r.base.map(x.retrait_croise).fillna(r.retrait), errors="coerce")
r["niveau"] = r.base.map(x.niveau).fillna("Brickset seul")
r["sources"] = r.base.map(x.sources).fillna("Brickset")
l = r[(r.statut == "en vente") & (r.categorie_bs == "Normal") & (r.prix_public >= 10)
      & r.rt.notna() & (r.rt <= auj + pd.DateOffset(months=10))].copy()
l["remise_min"] = 1 - l.prix_max / l.prix_public
l["gain_a_moins_20"] = [net(pg, pp * 0.8) if pg == pg else None for pg, pp in zip(l.pv_garde, l.prix_public)]
ordre = {"très probable": 0, "probable": 1, "à confirmer": 2, "Brickset seul": 2}
l["o"] = l.niveau.map(ordre)
l = l.sort_values(["rt", "o", "exclusif", "remise_min"], ascending=[True, True, False, True])
cols = {"base": "Set", "name": "Nom", "theme_bs": "Gamme", "niveau": "Retrait", "sources": "Sources",
        "exclusif": "Exclusivité Lego",
        "prix_public": "Prix public (€)", "prix_max": "Achète jusqu'à (€)", "remise_min": "Remise mini",
        "pv_garde": "Revente après retrait (€)", "gain_a_moins_20": "Gain net si acheté −20 % (€)",
        "j_garde": "Revente dans (jours)", "fiabilite": "Fiabilité", "chaleur_dealabs": "Chaleur Dealabs"}
f = DATA.parent / "black-friday" / "sets_en_fin_de_vie.xlsx"
with pd.ExcelWriter(f, engine="openpyxl") as w:
    for date, g in l.groupby("rt"):
        nom = f"Retrait {date:%d-%m-%Y}"
        t = g[list(cols)].rename(columns=cols).round(2)
        t["Exclusivité Lego"] = t["Exclusivité Lego"].map({True: "oui", False: ""})
        t.to_excel(w, index=False, sheet_name=nom)
        ws = w.sheets[nom]
        for c in ws["I"][1:]:
            c.number_format = "0%"
        for col, larg in zip("ABCDEFGHIJKLMN", [9, 40, 16, 13, 30, 10, 12, 14, 10, 16, 16, 12, 11, 12]):
            ws.column_dimensions[col].width = larg
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions
for date, g in l.groupby("rt"):
    print(f"{date:%d/%m/%Y} : {len(g)} sets ({g.niveau.value_counts().to_dict()}), dont {g.exclusif.sum()} exclusivités Lego ; "
          f"{(g.gain_a_moins_20 > 5).sum()} ont un gain net > 5 € achetés à −20 %")
