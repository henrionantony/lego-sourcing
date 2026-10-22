"""Rejoue les achats de sets entiers de l'historique dans le moteur.

Le prix de vente attendu est ici le prix réellement obtenu (oracle) : le test
mesure le calibrage des seuils, pas la qualité des estimations de prix.
Usage : python3 backtest.py <fichier des ventes>.xlsx
"""
import sys
import pandas as pd
from regles import juger, net

f = sys.argv[1]
inv = pd.read_excel(f, sheet_name=0)
ventes = pd.read_excel(f, sheet_name=1).iloc[:, :28]
inv["strat"] = inv["Strategie vente"].astype(str).str.strip()
sets = inv[(inv.Categorie == "SET") & (inv.strat == "ENTIER") & (inv["Prix achat"] > 0)].copy()
sets["cout_unitaire"] = sets["Prix achat"] / sets["Quantite"]

v = ventes[ventes.Produit == "SET"].merge(
    sets[["ID", "ID set", "Designation", "cout_unitaire", "Date achat", "Lieu achat"]],
    left_on="ID Achat", right_on="ID")
v["net"] = [net(pv, c) for pv, c in zip(v["Prix Vente"], v["cout_unitaire"])]
v["verdict"] = [juger(c, pv).decision for c, pv in zip(v["cout_unitaire"], v["Prix Vente"])]
v["gagnant"] = v["net"] > 0

print(f"{len(v)} ventes de sets entiers rejouées (prix de vente avant commissions)\n")
t = v.groupby(["verdict", "gagnant"]).agg(ventes=("net", "size"), net_total=("net", "sum")).round(0)
print(t.to_string(), "\n")
rates = v[v.gagnant & (v.verdict == "non")]
print(f"Gagnants que les seuils auraient rejetés : {len(rates)}, {rates.net.sum():.0f} € nets")
print(rates.sort_values("net", ascending=False)[["Designation", "Lieu achat", "cout_unitaire", "Prix Vente", "net"]].head(10).round(2).to_string(index=False))
