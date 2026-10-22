"""Liste Black Friday : pour chaque set en vente, le prix maximal d'achat et la
remise minimale qu'il faut pour que l'outil dise « achète ».

Usage : python3 liste_bf.py  → ../black-friday/liste_black_friday.xlsx
"""
import datetime as dt

import pandas as pd

from referentiel import DATA, charger
from regles import BANDES

r = charger()
aujourdhui = pd.Timestamp(dt.date.today())
retrait = pd.to_datetime(r.retrait, errors="coerce")
# En vente pour de vrai : sorti depuis 2023, pas encore retiré.
l = r[(r.statut == "en vente") & (r.year >= 2023) & (r.categorie_bs == "Normal")
      & (r.prix_public >= 15) & r.prix_max.notna() & ~(retrait < aujourdhui)].copy()
l["remise_min"] = 1 - l.prix_max / l.prix_public
l["a_regarder_jusqua"] = l.prix_max * (1 + l.fiabilite.map(BANDES).fillna(0.15))
l = l.sort_values("remise_min")
sortie = l[["base", "name", "theme_bs", "prix_public", "prix_vente_estime", "prix_max",
            "remise_min", "a_regarder_jusqua", "fiabilite", "retrait", "chaleur_dealabs"]]
sortie.columns = ["Set", "Nom", "Gamme", "Prix public (€)", "Revente estimée (€)", "Achète jusqu'à (€)",
                  "Remise mini", "À regarder jusqu'à (€)", "Fiabilité", "Retrait prévu", "Chaleur Dealabs"]
f = DATA.parent / "black-friday" / "liste_black_friday.xlsx"
with pd.ExcelWriter(f, engine="openpyxl") as w:
    sortie.round(2).to_excel(w, index=False, sheet_name="Black Friday")
    ws = w.sheets["Black Friday"]
    for c in ws["G"][1:]:
        c.number_format = "0%"
    for col, larg in zip("ABCDEFGHIJK", [9, 40, 18, 12, 14, 14, 10, 16, 12, 12, 12]):
        ws.column_dimensions[col].width = larg
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
print(len(sortie), "sets ;", (sortie["Remise mini"] <= 0.30).sum(), "achetables avec 30 % de remise ou moins")
print(sortie.head(10)[["Set", "Nom", "Prix public (€)", "Achète jusqu'à (€)", "Remise mini", "Fiabilité"]].to_string())
