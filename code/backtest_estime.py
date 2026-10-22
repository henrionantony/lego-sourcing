"""Rejoue l'historique avec des prix de vente ESTIMÉS, sans connaître la vente.

Pour chaque vente de set entier, on se place le jour de l'achat et on oublie
toutes les ventes de ce set (apprentissage « sans ce set ») :
- modèle « prix public » : prix public Lego × coefficient appris sur les
  ventes des autres sets (par statut du set à l'achat, par gamme) ;
- modèle « BrickEconomy » : valeur neuf BrickEconomy connue le jour de
  l'achat (historique disponible depuis fin novembre 2025 seulement) × rapport
  vente réelle / valeur BrickEconomy appris sur les autres sets ;
- modèle « BrickLink » : prix moyen des ventes conclues en Europe sur les 6
  derniers mois × rapport vente réelle / BrickLink appris sur les autres sets.
  BrickLink n'a pas d'historique : c'est la valeur d'aujourd'hui, donc un peu
  optimiste pour les ventes anciennes (on mesure aussi sur 2026 seul).
On mesure l'erreur de chaque source seule, puis on les combine, pondérées
par 1 / erreur² (referentiel.estimer).

Usage : python3 backtest_estime.py <fichier des ventes>.xlsx
"""
import bisect
import json
import sys

import pandas as pd

import bricklink
import referentiel as r
from regles import juger, net

MIN_VENTES_APPRISES = 20

xlsx = sys.argv[1]
ref = r.charger()
v = r.joindre(r.ventes_sets_entiers(xlsx), ref).sort_values("Date achat").reset_index(drop=True)
be = json.loads((r.CACHE / "brickeconomy.json").read_text())


def valeur_be_au(set_num, date):
    """Dernière valeur BrickEconomy publiée au plus tard à cette date."""
    h = sorted((be.get(set_num) or {}).get("historique") or [])
    i = bisect.bisect_right([d for d, _ in h], date.strftime("%Y-%m-%d"))
    return h[i - 1][1] if i else None


v["valeur_be_achat"] = [valeur_be_au(s, d) for s, d in zip(v.set_num, v["Date achat"])]
bl = json.loads(f.read_text()) if (f := r.CACHE / "bricklink.json").exists() else {}
v["valeur_bl"] = pd.Series([bricklink.valeur(bl.get(f"{s}|sold")) for s in v.set_num], dtype=float)

# Estimations de chaque source seule, apprises sans le set testé.
seules = {"gamme": [], "global": [], "BrickEconomy": [], "BrickLink": []}
ratios = []
for _, x in v.iterrows():
    avant = v[v.set_num != x.set_num] if pd.notna(x.set_num) else v[v.base != x.base]
    vb = avant[avant.valeur_be_achat > 0]
    ratio_be = (vb["Prix Vente"] / vb.valeur_be_achat).median() if len(vb) >= 5 else 1.0
    vl = avant[avant.valeur_bl > 0]
    ratio_bl = (vl["Prix Vente"] / vl.valeur_bl).median() if len(vl) >= 5 else 1.0
    coef = r.coefficients(avant) if len(avant) >= MIN_VENTES_APPRISES else None
    ratios.append((coef, ratio_be, ratio_bl))
    pp, f = (r.estimer(x.prix_public, x.statut_achat, x.Gamme, coef) if coef else (None, None))
    for s in ("gamme", "global"):
        seules[s].append(pp if f == s else None)
    seules["BrickEconomy"].append(x.valeur_be_achat * ratio_be if x.valeur_be_achat > 0 else None)
    seules["BrickLink"].append(x.valeur_bl * ratio_bl if x.valeur_bl > 0 else None)

print("Erreur de chaque source seule (|estimé / réel − 1|) :")
erreurs = {}
recent = v["Date Vente"] >= "2026-01-01"
for s, e in seules.items():
    e = pd.Series(e, dtype=float)
    err = (e / v["Prix Vente"] - 1).abs()
    erreurs[s] = err.median() if err.notna().sum() >= 10 else r.ERREURS[s]
    print(f"  {s:13s} {err.notna().sum():3d} ventes, médiane {err.median():.0%}, "
          f"3e quartile {err.quantile(.75):.0%} ; ventes 2026 : médiane {err[recent].median():.0%}")
print()

est, src = [], []
for (_, x), (coef, ratio_be, ratio_bl) in zip(v.iterrows(), ratios):
    e, f = None, "non chiffré"
    if coef:
        e, f = r.estimer(x.prix_public, x.statut_achat, x.Gamme, coef,
                         x.valeur_be_achat if pd.notna(x.valeur_be_achat) else None, ratio_be,
                         x.valeur_bl if pd.notna(x.valeur_bl) else None, ratio_bl, erreurs)
    est.append(e)
    src.append(f)
v["prix_estime"], v["source"] = est, src

# Correcteur demande Dealabs, appris sans le set testé.
v["chaleur_dealabs"] = v.base.map(ref.drop_duplicates("base").set_index("base").theme_rb).map(
    r.chaleur_dealabs(ref))
v["froid"] = v.chaleur_dealabs < r.SEUIL_FROID
rapport = v["Prix Vente"] / v.prix_estime
avant_dealabs = v.prix_estime.copy()
for i, x in v[v.froid & v.prix_estime.notna()].iterrows():
    autres = v.froid & (v.base != x.base) & v.prix_estime.notna()
    v.loc[i, "prix_estime"] = x.prix_estime * rapport[autres].median()
f = v.froid & v.prix_estime.notna()
print(f"Gammes froides sur Dealabs (< {r.SEUIL_FROID}°) : {f.sum()} ventes, erreur médiane "
      f"{(avant_dealabs[f] / v['Prix Vente'][f] - 1).abs().median():.0%} sans correcteur, "
      f"{(v.prix_estime[f] / v['Prix Vente'][f] - 1).abs().median():.0%} avec\n")
v["net"] = [net(pv, c) for pv, c in zip(v["Prix Vente"], v.cout_unitaire)]
v["gagnant"] = v.net > 0
v["verdict"] = [juger(c, e if pd.notna(e) else None, fiabilite=f).decision
                for c, e, f in zip(v.cout_unitaire, v.prix_estime, v.source)]
v["verdict_oracle"] = [juger(c, pv).decision for c, pv in zip(v.cout_unitaire, v["Prix Vente"])]

print(f"{len(v)} ventes de sets entiers rejouées, achats du {v['Date achat'].min():%d/%m/%Y} "
      f"au {v['Date achat'].max():%d/%m/%Y}\n")
print("Source de l'estimation :", v.source.value_counts().to_dict(), "\n")
c = v[v.prix_estime.notna()]
err = (c.prix_estime / c["Prix Vente"] - 1)
print("Erreur d'estimation (estimé / réel − 1) par source :")
print(c.assign(err=err.abs()).groupby("source").err.describe()[["count", "50%", "75%"]]
      .rename(columns={"50%": "médiane", "75%": "3e quartile"}).round(2).to_string(), "\n")
print("Verdicts avec prix estimés :")
t = v.groupby(["verdict", "gagnant"]).agg(ventes=("net", "size"), net_total=("net", "sum")).round(0)
print(t.to_string(), "\n")
print("Comparaison avec le test oracle (prix réel connu) :")
print(pd.crosstab(v.verdict_oracle, v.verdict, margins=True).to_string(), "\n")
rates = v[v.gagnant & (v.verdict == "non")]
print(f"Gagnants rejetés : {len(rates)}, {rates.net.sum():.0f} € nets")
print(rates.sort_values("net", ascending=False)[["Designation", "cout_unitaire", "prix_estime", "Prix Vente", "net", "source"]]
      .head(12).round(2).to_string(index=False), "\n")
pieges = v[~v.gagnant & (v.verdict == "achète")]
print(f"Perdants marqués « achète » : {len(pieges)}, {pieges.net.sum():.0f} € nets")
print(pieges.sort_values("net")[["Designation", "cout_unitaire", "prix_estime", "Prix Vente", "net", "source"]]
      .head(12).round(2).to_string(index=False))
v.to_csv(r.DATA / "backtest_estime.csv", index=False)
