"""Liste d'actions de la semaine (chaque lundi) : quoi réafficher, quoi baisser,
quoi mettre aussi sur Leboncoin, quels sets guetter pour le Black Friday.

Applique les règles de strategie-revente.md (baisse de 5 % par palier de
30 jours, planchers, exceptions) pour sortir l'émotion de la décision.
Lit l'export des annonces Vinted (extension), la base et le référentiel.
Écrit le détail dans ../stock/actions/ (privé) et envoie le résumé sur Telegram.

Usage : python3 actions_semaine.py [export_vinted.csv] [--essai]
"""
import datetime as dt
import math
import re
import sys
from pathlib import Path

import pandas as pd

import base
from alertes import euros, telegram
from referentiel import charger
from regles import MARGE_BRUTE_PLANCHER, PRELEVEMENTS, plafond_catalogue

STOCK = Path(__file__).resolve().parent.parent / "stock"
PALIER, BAISSE = 30, 0.05       # −5 % tous les 30 jours sans vente
AGE_MIN_BAISSE = 90             # pas de baisse sur un achat de moins de 3 mois


def numero(titre: str) -> str | None:
    m = re.findall(r"\b(\d{4,7})\b", str(titre))
    return m[-1] if m else None


def dernier_export() -> Path:
    return sorted((STOCK / "clemz").glob("vinted_annonces_*.csv"))[-1]


def stock_detaille() -> pd.DataFrame:
    """Articles encore en stock, avec le prix du set entier et l'état de remboursement de leur commande."""
    st = pd.DataFrame(base.lire("stock", "select=id,achat_id,id_set,prix_achat,date_achat,reste,strategie"
                                         "&reste=gt.0&id_set=not.is.null&limit=10000"))
    if st.empty:
        return st
    art = pd.DataFrame(base.lire("articles", "select=id,achat_id,prix_achat,quantite,quote_part&limit=10000"))
    ven = pd.DataFrame(base.lire("ventes", "select=article_id,prix_vente,quantite&limit=20000"))
    cout = (art.prix_achat.fillna(0) * art.quantite.fillna(1)).groupby(art.achat_id).sum()
    ven = ven.merge(art[["id", "achat_id"]], left_on="article_id", right_on="id")
    encaisse = (ven.prix_vente.fillna(0) * (1 - PRELEVEMENTS)).groupby(ven.achat_id).sum()
    st["rembourse"] = st.achat_id.map(encaisse).fillna(0) >= st.achat_id.map(cout).fillna(0)
    q = pd.to_numeric(st.id.map(art.set_index("id").quote_part), errors="coerce")
    st["cout_set"] = st.prix_achat / q.where(q > 0, 1)
    st["set"] = st.id_set.astype(str).str.split("-").str[0].str.removesuffix(".0")
    st["age"] = (pd.Timestamp.today() - pd.to_datetime(st.date_achat)).dt.days
    return st


def deja_baisse() -> set[str]:
    """Annonces dont le prix a été changé il y a moins de PALIER jours (historique dans la base)."""
    depuis = (dt.date.today() - dt.timedelta(days=PALIER)).isoformat()
    ids = {r["annonce_id"] for r in base.lire("prix_annonces", f"select=annonce_id&le=gte.{depuis}&limit=10000")}
    return {r["url"] for r in base.lire("annonces", "select=id,url&limit=10000") if r["id"] in ids}


def exception(x, aujourdhui) -> str | None:
    """Raison de ne pas baisser (stratégie), ou None."""
    if aujourdhui.month == 12:
        return "décembre : pas de baisse"
    rt = pd.to_datetime(x.get("retrait_croise") or x.get("retrait"), errors="coerce") if x is not None else pd.NaT
    if rt == rt and x.get("statut") == "retiré":
        if aujourdhui - rt < pd.Timedelta(days=183):
            return "retiré depuis moins de 6 mois"
        if abs((rt + pd.DateOffset(years=2)) - aujourdhui) < pd.Timedelta(days=183):
            return "proche du pivot des 2 ans"
    return None


def construire(export: Path):
    auj = pd.Timestamp.today().normalize()
    ann = pd.read_csv(export, sep=";", dtype=str, encoding="utf-8-sig")
    ann["prix"] = pd.to_numeric(ann.prix, errors="coerce")
    ann["favoris"] = pd.to_numeric(ann.favoris, errors="coerce").fillna(0).astype(int)
    ann["set"] = ann.titre.map(numero)
    ref = charger().drop_duplicates("base").set_index("base")
    st = stock_detaille()
    recents = deja_baisse()

    # 1. Baisses (annonces en ligne d'un set encore en stock)
    baisses, gardes = [], []
    par_set = st.groupby("set").agg(cout=("cout_set", "max"), age=("age", "max"), rembourse=("rembourse", "all")) \
        if not st.empty else pd.DataFrame()
    for a in ann[ann.statut == "en ligne"].itertuples():
        if a.set not in par_set.index or a.prix != a.prix:
            continue
        s = par_set.loc[a.set]
        x = ref.loc[a.set] if a.set in ref.index else None
        raison = exception(x, auj)
        if a.url in recents:
            raison = "déjà baissée ce mois-ci"
        if s.age < AGE_MIN_BAISSE:
            raison = raison or "acheté il y a moins de 3 mois"
        plancher = s.cout / (1 - PRELEVEMENTS)                      # jamais à perte
        if not s.rembourse and s.age < 365:
            plancher = max(plancher, s.cout * MARGE_BRUTE_PLANCHER)  # plancher tant que le lot n'est pas remboursé
        nouveau = round(a.prix * (1 - BAISSE))
        if not raison and nouveau < plancher:
            raison = "au plancher"
        # Encore au catalogue et plus cher que chez LEGO une fois frais et port payés :
        # on ramène au plafond tout de suite (pas d'attente d'âge ni de mois), sans passer sous le plancher
        cap = plafond_catalogue(x.get("prix_public")) if x is not None and x.get("statut") == "en vente" else None
        if cap and a.prix > cap and max(cap, math.ceil(plancher)) < a.prix:
            nouveau, raison = max(cap, math.ceil(plancher)), None
        ligne = {"Annonce": a.titre, "Set": a.set, "Prix actuel": a.prix, "Nouveau prix": nouveau,
                 "Plancher": round(plancher, 2), "Favoris": a.favoris, "Âge du stock (j)": int(s.age),
                 "Lot remboursé": "oui" if s.rembourse else "non", "Lien": a.url}
        (gardes if raison else baisses).append({**ligne, "Pourquoi on garde": raison} if raison else ligne)
    baisses = pd.DataFrame(baisses)
    if not baisses.empty:
        baisses = baisses.sort_values(["Favoris", "Âge du stock (j)"], ascending=False)

    # 2. Annonces masquées à vérifier puis réafficher, les plus chères et les plus suivies d'abord
    masq = ann[ann.statut == "masqué"].assign(score=lambda d: d.prix.fillna(0) * (1 + d.favoris / 5)) \
        .sort_values("score", ascending=False)[["titre", "prix", "favoris", "url"]]
    masq.columns = ["Annonce", "Prix", "Favoris", "Lien"]

    # 3. À mettre aussi sur Leboncoin : les annonces en ligne qui attirent le plus
    lbc = ann[ann.statut == "en ligne"].assign(score=lambda d: d.prix.fillna(0) * (1 + d.favoris)) \
        .sort_values("score", ascending=False).head(40)[["titre", "prix", "favoris", "url"]]
    lbc.columns = ["Annonce", "Prix Vinted", "Favoris", "Lien"]

    # 4. Black Friday : sets qui quittent le catalogue d'ici fin d'année, très demandés
    r = ref.reset_index()
    rt = pd.to_datetime(r.retrait_croise.fillna(r.retrait), errors="coerce")
    bf = r[(r.statut == "en vente") & (rt <= pd.Timestamp(auj.year, 12, 31)) & (r.demande == "forte")
           & r.prix_max.notna() & r.prix_public.notna()].copy()
    bf["Remise nécessaire"] = 1 - bf.prix_max / bf.prix_public
    bf = bf.sort_values("Remise nécessaire").head(30)
    bf = pd.DataFrame({"Set": bf.base, "Nom": bf.name, "Prix public": bf.prix_public, "Achète jusqu'à": bf.prix_max,
                       "Remise nécessaire": bf["Remise nécessaire"].round(2), "Retrait": rt[bf.index].dt.strftime("%d/%m/%Y"),
                       "Exclusivité Lego": bf.exclusif})
    return baisses, pd.DataFrame(gardes), masq, lbc, bf


def enregistrer(export: Path, baisses: pd.DataFrame):
    """Garde les annonces et les baisses proposées dans la base : la semaine
    suivante, une annonce déjà baissée n'est pas re-baissée avant 30 jours."""
    ann = pd.read_csv(export, sep=";", dtype=str, encoding="utf-8-sig")
    lignes = [{"plateforme": "Vinted", "url": a.url, "titre": a.titre, "id_set": numero(a.titre),
               "prix": float(a.prix) if a.prix == a.prix else None,
               "statut": {"en ligne": "en ligne", "vendu": "vendue"}.get(a.statut, "retirée" if a.statut == "masqué" else a.statut)}
              for a in ann.itertuples() if isinstance(a.url, str)]
    for i in range(0, len(lignes), 500):
        base.ajouter("annonces?on_conflict=url", lignes[i:i + 500], fusion=True)
    if baisses.empty:
        return
    ids = {r["url"]: r["id"] for r in base.lire("annonces", "select=id,url&limit=10000")}
    base.ajouter("prix_annonces", [{"annonce_id": ids[u], "prix": float(p), "raison": "baisse 5 % proposée"}
                                   for u, p in zip(baisses.Lien, baisses["Nouveau prix"]) if u in ids])


def message(baisses, masq, lbc, bf) -> str:
    l = [f"📋 Actions de la semaine du {dt.date.today():%d/%m}"]
    l.append(f"\n1️⃣ Vérifie et réaffiche les annonces masquées : {len(masq)} ({euros(masq.Prix.sum())} affichés). "
             "Commence par celles-ci :")
    l += [f"• {t[:60]} · {euros(p)}" for t, p in zip(masq.Annonce.head(8), masq.Prix.head(8))]
    l.append(f"\n2️⃣ Baisse de 5 % : {len(baisses)} annonces"
             + (f" (−{euros((baisses['Prix actuel'] - baisses['Nouveau prix']).sum())} au total)" if len(baisses) else ""))
    if len(baisses):
        l += [f"• {t[:50]} : {euros(a)} → {euros(n)}" for t, a, n in
              zip(baisses.Annonce.head(10), baisses["Prix actuel"].head(10), baisses["Nouveau prix"].head(10))]
    l.append(f"\n3️⃣ À mettre aussi sur Leboncoin, en proposant d'abord la remise en main propre (les plus chères et les plus demandées) :")
    l += [f"• {t[:60]} · {euros(p)}" for t, p in zip(lbc.Annonce.head(8), lbc["Prix Vinted"].head(8))]
    l.append(f"\n4️⃣ À guetter pour le Black Friday (quittent le catalogue, très demandés) :")
    l += [f"• {s} {n[:40]} : achète jusqu'à {euros(p)} ({-r:+.0%} du prix public)"
          for s, n, p, r in zip(bf.Set.head(8), bf.Nom.head(8), bf["Achète jusqu'à"].head(8), bf["Remise nécessaire"].head(8))]
    l.append("\nLe détail complet est dans le fichier Excel de la semaine.")
    return "\n".join(l)


def main():
    essai = "--essai" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    export = Path(args[0]) if args else dernier_export()
    baisses, gardes, masq, lbc, bf = construire(export)
    sortie = STOCK / "actions" / f"actions_semaine_{dt.date.today():%Y-%m-%d}.xlsx"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(sortie) as w:
        for nom, d in [("Réafficher", masq), ("Baisser de 5 %", baisses), ("Garder le prix", gardes),
                       ("Leboncoin", lbc), ("Black Friday", bf)]:
            d.to_excel(w, sheet_name=nom, index=False)
    if not essai:
        enregistrer(export, baisses)
    texte = message(baisses, masq, lbc, bf)
    telegram(texte[:4000], True, essai)
    print(f"{sortie} : {len(masq)} à réafficher, {len(baisses)} baisses, {len(gardes)} gardées, {len(bf)} sets Black Friday")
    return sortie


if __name__ == "__main__":
    main()
