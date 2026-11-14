"""Vérifie la cote BrickEconomy des sets arrivés en deal sans cote connue (04/11).

Les alertes tournent sur GitHub sans clé BrickEconomy : un set jamais relevé part donc
sans ce contrôle (Mario & Yoshi 71438, Plante Piranha 71426 le 04/11). Ce script tourne au
passage horaire : il relève la cote de ces sets sur une réserve de 10 appels par jour,
la range dans le référentiel et, si le marché ne paie pas le prix du deal, envoie une
correction courte sur Telegram (une seule fois par deal).

    python3 verif_be.py          → vérifie les deals des 6 dernières heures
    python3 verif_be.py essai    → affiche sans envoyer ni écrire
"""
import datetime as dt
import json
import sys

import pandas as pd

import alertes
import base
import referentiel
import regles

FAITS = referentiel.CACHE / "be_verifies.json"


def main(essai: bool):
    depuis = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")
    from urllib.parse import quote
    deals = base.lire("deals", "select=id,titre,lien,prix,verdict,id_set"
                      f"&vu_le=gte.{depuis}&verdict=in.({quote('achète')},{quote('à regarder')})")
    faits = json.loads(FAITS.read_text()) if FAITS.exists() else {}
    ref = referentiel.charger()
    be = referentiel.BrickEconomy()
    try:
        ratios = json.loads((referentiel.CACHE / "ratios_be.json").read_text())
    except (OSError, ValueError):
        ratios = {}
    change = False
    for d in deals:
        s, prix = str(d.get("id_set") or ""), d.get("prix")
        if not s or not prix or str(d["id"]) in faits:
            continue
        lignes = ref.index[ref.base == s]
        if not len(lignes):
            continue
        i = lignes[0]
        x = ref.loc[i]
        if x.get("be_fr") == x.get("be_fr") and x.get("be_fr"):
            continue   # cote déjà connue : l'alerte l'a déjà prise en compte
        if be.restant(alerte=True) <= 0 and x.set_num not in be.cache:
            print(f"{s} : plus d'appels BrickEconomy aujourd'hui")
            continue
        e = be.valeur(x.set_num, max_age_jours=30, alerte=True)
        bf, bt = referentiel.cote_be(e, ratios.get(str(x.statut), ratios.get("global", 1.0)))
        faits[str(d["id"])] = {"set": s, "le": dt.date.today().isoformat(), "be_fr": bf}
        change = True
        if bf is None:
            print(f"{s} : pas de cote BrickEconomy")
            continue
        ref.loc[i, ["be_fr", "be_tendance"]] = [bf, bt]
        freins = []
        pl = regles.plancher(lego_com="lego" in str(d.get("lien") or "").lower() and "(lego" in str(d.get("titre") or "").lower())
        if bf / prix < pl:
            freins.append(f"la cote BrickEconomy ne laisse que {bf / prix - 1:+.0%} brut, sous ton plancher")
        if bt is not None and bt <= alertes.BAISSE_COTE:
            freins.append(f"sa cote baisse ({bt:.0%} sur 6 mois)")
        print(f"{s} à {prix} € : cote France {bf} €, tendance {bt} → {freins or 'ok'}")
        if freins and bf / prix < pl:
            texte = (f"👎 Correction : {d['titre']} à {alertes.euros(prix)}\n"
                     f"Finalement pas un bon coup : {' ; '.join(freins)}.\n{d.get('lien') or ''}")
            alertes.telegram(texte, sonne=False, essai=essai)
            if not essai:
                base.modifier("deals", f"id=eq.{d['id']}", {"verdict": "non"})
            print("correction envoyée" if not essai else "correction (essai)")
    if change and not essai:
        ref.to_csv(referentiel.DATA / "referentiel.csv", index=False)
        FAITS.write_text(json.dumps(faits))


if __name__ == "__main__":
    main("essai" in sys.argv)
