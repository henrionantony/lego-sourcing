"""Plan Black Friday LEGO.com : pour chaque set qui part à la retraite le 31/12, ce qu'il
rapporte selon la promo (prix plein, -20 %, -30 %), entier ou en part-out.

Règles (strategie-revente.md) : marge BRUTE par transaction, au moins le plancher privé ;
les points Insiders et le cashback ne sont pas déduits du prix (bonus à part) ;
set offert = marge de sécurité, compté à part ; part-out = figurines à la cote +5 à 10 €,
construction au pire 20 % du prix catalogue.
Si un export LEGO.com de l'extension (stock/lego/lego_*.csv) existe, le prix et la dispo
du site remplacent le prix public.

Usage : python3 plan_bf.py  → ../black-friday/plan_black_friday_2026.xlsx + ../black-friday/plan_bf.json
"""
import csv
import json
from pathlib import Path

import pandas as pd

import base
import regles
from referentiel import CACHE, DATA, charger

ICI = Path(__file__).resolve().parent
LEGO = ICI.parent
SORTIE = LEGO / "black-friday"
PLANCHER = regles.MARGE_BRUTE_PLANCHER - 1   # marge brute mini par transaction (réglage privé)
REMISES = [0, 0.20, 0.30]
PTS_PAR_EURO = 7.5 * 2    # points Insiders doublés au Black Friday
EURO_PAR_PT = 5 / 750
BONUS_FIG = 7.5           # figurine vendue à la cote + 5 à 10 €
BUILD_PIRE = 0.20         # construction bradée en vrac
CADEAU = regles.GWP_DEFAUT / 170   # set offert dès 170 € de panier (BF 2025) : sa revente, par euro dépensé


def export_lego() -> dict:
    """Dernier export LEGO.com de l'extension, s'il y en a : {set: {prix, prix_barre, dispo, badges}}."""
    fichiers = sorted((LEGO / "stock" / "lego").glob("lego_*.csv"), key=lambda f: f.stat().st_mtime)
    out = {}
    for f in fichiers:   # du plus ancien au plus récent : le dernier relevé gagne
        with open(f, encoding="utf-8-sig") as h:
            for r in csv.DictReader(h, delimiter=";"):
                p = (r.get("prix") or "").replace(",", ".")
                out[r["set"]] = {"prix_site": float(p) if p else None, "dispo": r.get("dispo", ""),
                                 "badges": r.get("badges", ""), "export": f.name}
    return out


BE = json.loads((CACHE / "brickeconomy.json").read_text())


def figurines() -> tuple[dict, dict]:
    be = BE
    cotes = json.loads((CACHE / "brickeconomy_figurines.json").read_text())
    par_set = {k.split("-")[0]: (v.get("figurines") or {}).get("minifigs_quantities") or {}
               for k, v in be.items() if v.get("figurines")}
    return par_set, {k.lower(): v.get("valeur_neuf") for k, v in cotes.items()}


def nouveautes(r, figs_set, cotes, stock) -> pd.DataFrame:
    """Sets sortis depuis juin 2026 avec au moins 4 figurines : intérêt du part-out."""
    inv = pd.read_csv(CACHE / "inventories.csv.gz").sort_values("version").drop_duplicates("set_num", keep="last")
    im = pd.read_csv(CACHE / "inventory_minifigs.csv.gz").merge(inv[["id", "set_num"]], left_on="inventory_id", right_on="id")
    nb = im.groupby("set_num").quantity.sum()
    n = r[(r.sortie.astype(str) >= "2026-06") & (r.prix_public >= 20)].drop_duplicates("base").copy()
    n["nf"] = n.set_num.map(nb)
    lignes = []
    for _, x in n[n.nf >= 4].iterrows():
        s, prix = str(x.base), float(x.prix_public)
        figs = figs_set.get(s) or {}
        connues = {c: q for c, q in figs.items() if cotes.get(c.lower())}
        val = sum((cotes[c.lower()] + BONUS_FIG) * q for c, q in connues.items())
        complet = bool(figs) and len(connues) == len(figs)
        po = val + BUILD_PIRE * prix if complet else None
        d = {"Set": s, "Nom": x["name"], "Gamme": x.theme_bs, "Sortie": x.sortie, "Exclu LEGO": "oui" if x.exclusif is True else "",
             "Demande": x.demande, "En stock": stock.get(s, 0), "Prix LEGO (€)": prix, "Figurines": int(x.nf),
             "Cotées": f"{len(connues)}/{len(figs)}" if figs else "pas encore relevé",
             "Figurines à la cote + 7,50 € (€)": round(val, 2) if complet else None,
             "Part-out pire cas (€)": round(po, 2) if po else None}
        for rem in REMISES:
            d[f"Marge part-out {int(rem*100)} %"] = round(po / (prix * (1 - rem)) - 1, 3) if po else None
        lignes.append(d)
    return pd.DataFrame(lignes).sort_values(["Figurines"], ascending=False)


def main():
    r = charger()
    site = export_lego()
    # Sets qui partent le 31/12 selon nos sources + tous ceux de la page LEGO.com « Bientôt retirés »
    l = r[(r.retrait.astype(str).str.startswith("2026-12-31") | r.base.astype(str).isin(site))
          & (r.prix_public >= 10)].drop_duplicates("base").copy()
    stock = base.stock_par_set() or {}
    figs_set, cotes = figurines()
    lignes = []
    for _, x in l.iterrows():
        s = str(x.base)
        info = site.get(s, {})
        prix = info.get("prix_site") or float(x.prix_public)
        # Revente prudente : la plus basse entre notre modèle (gamme × tes ventes) et la prévision
        # BrickEconomy à 2 ans quand elle existe (le modèle seul est grossier : un coefficient par gamme)
        modele = float(x.pv_garde) if pd.notna(x.pv_garde) else None
        be = BE.get(s + "-1") or {}
        be2 = be.get("prevision_2ans")
        revente = min(v for v in (modele, be2) if v) if (modele or be2) else None
        source = "modèle + BrickEconomy" if modele and be2 else "BrickEconomy" if be2 else "modèle seul (gamme)"
        figs = figs_set.get(s) or {}
        connues = {c: q for c, q in figs.items() if cotes.get(c.lower())}
        val_figs = sum((cotes[c.lower()] + BONUS_FIG) * q for c, q in connues.items())
        po = (val_figs + BUILD_PIRE * float(x.prix_public)) if figs and len(connues) == len(figs) else None
        d = {"Set": s, "Nom": x["name"], "Gamme": x.theme_bs, "Retrait": x.niveau_retrait,
             "Exclu LEGO": "oui" if x.exclusif is True else "", "Demande": x.demande,
             "En stock": stock.get(s, 0), "Prix LEGO (€)": round(prix, 2),
             "Sur LEGO.com": (info.get("dispo") or "liste Bientôt retirés") if info else "pas sur la liste LEGO",
             "Revente après retrait (€)": round(revente, 2) if revente else None,
             "Source revente": source, "Cote BE aujourd'hui (€)": be.get("valeur_neuf"),
             "Vente en (jours)": x.j_garde,
             "Points x2 (€ de bon)": round(prix * PTS_PAR_EURO * EURO_PAR_PT, 2)}
        for rem in REMISES:
            cout = prix * (1 - rem)
            d[f"Marge entier {int(rem*100)} %"] = round(revente / cout - 1, 3) if revente else None
        d["Marge prix plein + cadeau"] = round((revente + prix * CADEAU) / prix - 1, 3) if revente else None
        d["Remise mini pour le plancher"] = round(1 - revente / (prix * (1 + PLANCHER)), 3) if revente else None
        d["Figurines"] = f"{len(connues)}/{len(figs)} cotées" if figs else (int(x.minifigs) if pd.notna(x.minifigs) else 0)
        d["Part-out pire cas (€)"] = round(po, 2) if po else None
        for rem in REMISES:
            d[f"Marge part-out {int(rem*100)} %"] = round(po / (prix * (1 - rem)) - 1, 3) if po else None
        # Verdict : meilleure option qui passe le plancher, à la plus petite remise
        verdict = "non"
        for rem in REMISES:
            me, mp = d[f"Marge entier {int(rem*100)} %"], d[f"Marge part-out {int(rem*100)} %"]
            if x.demande == "faible" and (mp or 0) < PLANCHER:
                continue
            if (me or 0) >= PLANCHER or (mp or 0) >= PLANCHER:
                quoi = "entier" if (me or 0) >= (mp or 0) else "part-out"
                verdict = f"oui {quoi}" + (f" dès -{int(rem*100)} %" if rem else " même au prix plein")
                break
        d["Verdict"] = verdict
        lignes.append(d)
    t = pd.DataFrame(lignes)
    nouv = nouveautes(r, figs_set, cotes, stock)
    ordre = {"oui": 0, "non": 1}
    t["_o"] = t.Verdict.str.split().str[0].map(ordre)
    t = t.sort_values(["_o", "Remise mini pour le plancher"]).drop(columns="_o")
    SORTIE.mkdir(exist_ok=True)
    f = SORTIE / "plan_black_friday_2026.xlsx"
    with pd.ExcelWriter(f, engine="openpyxl") as w:
        t.to_excel(w, index=False, sheet_name="Sets 31-12")
        ws = w.sheets["Sets 31-12"]
        for i, c in enumerate(t.columns, 1):
            if "Marge" in c or "Remise" in c:
                for cell in list(ws.iter_cols(min_col=i, max_col=i))[0][1:]:
                    cell.number_format = "0%"
            ws.column_dimensions[ws.cell(1, i).column_letter].width = 34 if c == "Nom" else 12
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions
        nouv.to_excel(w, index=False, sheet_name="Nouveautés part-out")
        pd.DataFrame({"Comment lire": [
            "Revente = la plus prudente entre notre modèle (coefficient par gamme, grossier) et la prévision BrickEconomy à 2 ans. « modèle seul » = à vérifier avant d'acheter.",
            f"Marge = revente estimée après retrait / prix payé - 1, en BRUT (avant les 21 %). Plancher +{PLANCHER:.0%}.",
            "Prix LEGO = prix relevé sur LEGO.com par l'extension s'il y en a, sinon prix catalogue.",
            "Points x2 et cashback : PAS déduits du prix (bonus gardé pour plus tard).",
            f"Set offert (dès 170 € / 250 € l'an dernier) : compté seulement dans « Marge prix plein + cadeau » (revente d'un cadeau par tranche de 170 €), car la marge se juge sur la commande entière.",
            "Part-out pire cas = figurines à la cote BrickEconomy + 7,50 € + construction à 20 % du prix catalogue.",
            "Demande faible : jamais « oui » en entier.",
            "Onglet Nouveautés part-out : sets sortis depuis juin 2026 avec au moins 4 figurines (inventaire Rebrickable). Les figurines d'une nouveauté valent souvent plus au début ; cotes BrickEconomy demandées en priorité.",
            f"Verdict = la plus petite remise où le set (entier ou part-out) atteint +{PLANCHER:.0%}.",
        ]}).to_excel(w, index=False, sheet_name="Lire")
    json.dump(t.where(t.notna(), None).to_dict("records"), open(SORTIE / "plan_bf.json", "w"), ensure_ascii=False)
    print(len(t), "sets ;", t.Verdict.value_counts().to_dict())
    return t


if __name__ == "__main__":
    main()
