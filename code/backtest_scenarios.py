"""Test du modèle à deux scénarios (revente rapide / garde jusqu'au retrait)
sur l'historique, sans connaître la vente : coefficients appris sans le set
testé, date de retrait de Brickset, verdict au prix d'achat réel.

Usage : python3 backtest_scenarios.py <fichier des ventes>.xlsx
"""
import sys

import pandas as pd

import referentiel as r
from regles import BANDES, juger, net, prix_max

ref = r.charger()
v = r.joindre(r.ventes_sets_entiers(sys.argv[1]), ref)
v = v[v.prix_public > 0].reset_index(drop=True)
v["net"] = [net(pv, c) for pv, c in zip(v["Prix Vente"], v.cout_unitaire)]
lignes = []
for _, x in v.iterrows():
    avant = v[v.base != x.base]
    if x.statut_achat == "en vente":
        sc = r.scenarios(x.prix_public, x.Gamme, x.retrait, r.coefficients_scenarios(avant),
                         pd.Timestamp(x["Date achat"]))
        pr, jr = sc["rapide"]
        m, choix, pv, j = prix_max(pr), "rapide", pr, jr
        if "garde" in sc:
            pg, jg = sc["garde"]
            mg = prix_max(pg)
            if mg > m and net(pg, mg) / mg * 365 / max(jg, 30) >= r.RENDEMENT_MIN_GARDE:
                m, choix, pv, j = mg, "garde", pg, jg
    else:
        coef = r.coefficients(avant)
        pv, _ = r.estimer(x.prix_public, "retiré", x.Gamme, coef)
        m, choix, j = prix_max(pv), "retiré", None
    reel = "rapide" if x.statut_vente == "en vente" and x.statut_achat == "en vente" else (
        "garde" if x.statut_achat == "en vente" else "retiré")
    lignes.append({"choix": choix, "reel": reel, "pv_estime": pv, "delai_estime": j,
                   "verdict": juger(x.cout_unitaire, pv, fiabilite="gamme").decision})
v = pd.concat([v, pd.DataFrame(lignes)], axis=1)
v["gagnant"] = v.net > 0
same = v[v.choix == v.reel]
print(f"{len(v)} ventes. Scénario choisi = scénario réel pour {len(same)} ventes ; "
      f"erreur médiane du prix sur celles-ci : {(same.pv_estime / same['Prix Vente'] - 1).abs().median():.0%}")
for nom, g in v.groupby("reel"):
    print(f"  vendu en « {nom} » : {len(g)} ventes, erreur médiane si on prévoit ce scénario "
          f"{(g[g.choix == nom].pv_estime / g[g.choix == nom]['Prix Vente'] - 1).abs().median():.0%}")
print("\nVerdicts :")
print(v.groupby(["verdict", "gagnant"]).agg(ventes=("net", "size"), net_total=("net", "sum")).round(0).to_string())
v.to_csv(r.DATA / "backtest_scenarios.csv", index=False)
