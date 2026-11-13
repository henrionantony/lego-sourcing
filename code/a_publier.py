"""Prix proposé pour les sets en stock qui ne sont publiés nulle part.

Prix = le plus haut entre l'estimation de revente (referentiel.csv) et le
plancher (coût d'achat × MARGE_BRUTE_PLANCHER, relâché quand le reste de l'achat compense : transaction.py).
Set encore au catalogue : plafonné pour que prix + frais acheteur Vinted + port
reste sous le prix LEGO (regles.plafond_catalogue), sans descendre sous le plancher.
Écrit ../stock/app/a_publier.json (privé, jamais dans le dépôt) :
{set: {prix, estime, cout, etat}} pour l'onglet Platforms de LEGO HQ.
"""
import csv
import json
import math
from pathlib import Path

import base
import regles
import transaction

ICI = Path(__file__).resolve().parent
LEGO = ICI.parent
SORTIE = LEGO / "stock" / "app" / "a_publier.json"
APP = SORTIE.parent
# Exemplaires en stock par set, le moins cher d'abord, pour rattacher une vente scannée
EXEMPLAIRES = LEGO / "stock" / "app" / "exemplaires.json"


def arrondi(p: float) -> float:
    """Prix d'annonce rond : au 5 € au-dessus dès 20 € (Vinted : prix ronds), à l'euro en dessous."""
    return float(math.ceil(p / 5) * 5) if p >= 20 else float(math.ceil(p))


def mes_ventes() -> dict[str, list]:
    """Tes ventes de ce set entier, [[date, prix unitaire, plateforme]], de la plus ancienne à la plus récente.
    Ventes co-investies laissées de côté : la base n'y garde que ta part du prix."""
    sets = {x["id"]: str(x["id_set"]).split("-")[0].removesuffix(".0")
            for x in base.lire("articles", "select=id,id_set&categorie=eq.SET&id_set=not.is.null&limit=10000") or []}
    out: dict[str, list] = {}
    for v in sorted(base.lire("ventes", "select=article_id,date_vente,prix_vente,quantite,co_investisseur,plateforme"
                              "&produit=eq.SET&limit=10000") or [], key=lambda v: v.get("date_vente") or ""):
        s = sets.get(v.get("article_id"))
        if s and v.get("prix_vente") and not v.get("co_investisseur"):
            out.setdefault(s, []).append([v.get("date_vente") or "", round(float(v["prix_vente"]) / max(1, int(v.get("quantite") or 1)), 2),
                                          (v.get("plateforme") or "").title()])
    return out


def main():
    ref = {}
    with open(LEGO / "data" / "referentiel.csv", newline="") as f:
        for r in csv.DictReader(f):
            ref.setdefault(r["set_num"].split("-")[0], r)
    # Date du dernier relevé BrickEconomy de chaque set : au-delà de 30 jours (ou jamais), l'estimation est moins sûre
    try:
        be = json.loads((LEGO / "data" / "cache" / "brickeconomy.json").read_text())
    except (OSError, ValueError):
        be = {}
    stock = base.lire("stock", "select=*&reste=gt.0&id_set=not.is.null&categorie=eq.SET&limit=10000")
    # Stratégie choisie dans l'appli (Post now / Keep until it retires), par article
    hz = {r["id"]: (r.get("horizon") or "", r.get("vise_le") or "")
          for r in base.lire("articles", "select=id,horizon,vise_le&horizon=not.is.null&limit=10000") or []}
    vendus = mes_ventes()
    # Plancher calculé sur l'achat entier : plus bas quand le reste de l'achat a déjà assez rapporté
    tx = transaction.planchers()
    planchers: dict[str, float] = {}
    couts: dict[str, list] = {}
    ex: dict[str, list] = {}
    for l in stock:
        s = str(l["id_set"]).split("-")[0].removesuffix(".0")
        part = float(l.get("quote_part") or 1) or 1
        # prix_achat = prix de la ligne entière : on le ramène à un exemplaire
        cout = 0.0 if l.get("offert") else float(l["prix_achat"] or 0) / part / max(1, int(l.get("quantite") or 1))
        couts.setdefault(s, []).append((cout, l.get("details_etat") or ""))
        if l["id"] in tx:
            planchers[s] = min(planchers.get(s, tx[l["id"]]["plancher"]), tx[l["id"]]["plancher"])
        ex.setdefault(s, []).append([l["id"], round(cout, 2), l.get("date_achat") or "", l.get("lieu") or "", int(l.get("reste") or 1), *hz.get(l["id"], ("", "")), l.get("emplacement") or "",
                                  {"LOT/DETAILS": "P", "ENTIER": "E"}.get(str(l.get("strategie") or "").strip().upper(), "")])
    ebay_vivantes = {k: v.get("prix") for k, v in (base.doc("ebay/annonces") or {}).items()
                     if isinstance(v, dict) and not v.get("retire") and v.get("prix")}
    # Prix en ligne sur chaque plateforme (le plus bas par set) : affichés côte à côte dans l'appli
    vinted_en_ligne, lbc_en_ligne = {}, {}
    try:
        for a in json.loads((APP / "annonces.json").read_text()):
            if a.get("s") == "en ligne" and a.get("n") and a.get("p"):
                vinted_en_ligne[a["n"]] = min(float(a["p"]), vinted_en_ligne.get(a["n"], 1e9))
    except Exception:
        pass
    try:
        import leboncoin
        exports = sorted((APP.parent / "leboncoin").glob("*.csv"))
        for a in (leboncoin.lire(exports[-1]) if exports else []):
            for n in set(a["sets"]):
                if a.get("p"):
                    lbc_en_ligne[n] = min(float(a["p"]), lbc_en_ligne.get(n, 1e9))
    except Exception:
        pass
    out = {}
    for s, c in couts.items():
        r = ref.get(s, {})
        estime = regles.estime_revente(r)
        cout = min(x[0] for x in c)
        # Coût bien au-dessus de l'estimation : souvent le prix d'un lot entier
        # posé sur un seul article. On garde l'estimation et on le signale.
        douteux = bool(estime) and cout > 2 * estime
        # Même calcul que l'alerte Telegram (regles.prix_annonce) : un seul prix partout
        pa = regles.prix_annonce(estime, cout, float(r.get("prix_public") or 0), r.get("statut") == "en vente", planchers.get(s), s)
        prix = arrondi(estime) if douteux else pa["prix"]
        # Annonce eBay en ligne à un prix plus haut (marché eBay croisé, validé) : même niveau sur Vinted et Leboncoin,
        # arrondi au 5 du dessous (prix ronds) ; jamais pour un set encore au catalogue (plafonné sous le prix LEGO)
        e = ebay_vivantes.get(s)
        if e and not pa["plafonne"] and not douteux and float(e) > (prix or 0):
            prix = float(max(5 * math.floor(float(e) / 5), prix or 0))
        # Pour choisir « poster maintenant » ou « garder » : revente rapide, revente après retraite, date de retraite
        conseil = {"be": (be.get(s + "-1") or {}).get("date") if (be.get(s + "-1") or {}).get("valeur_neuf") else None,
                   "src": r.get("fiabilite") or "",
                   "st": r.get("statut") or "", "rapide": float(r.get("pv_rapide") or 0) or None,
                   "garde": float(r.get("pv_garde") or 0) or None, "retrait": r.get("retrait_croise") or r.get("retrait") or "",
                   # Valeur attendue au pivot, 2 ans après le retrait (courbe apprise sur tes ventes après retrait)
                   "pivot": float(r.get("pv_pivot") or 0) or None, "pivot_le": r.get("pivot_le") or ""}
        out[s] = {"prix": prix, "ebay": regles.prix_ebay(prix) if prix else None, "estime": round(estime, 2) or None, **conseil,
                  "cout": round(cout, 2), "plancher": pa["plancher"], "pp": float(r.get("prix_public") or 0) or None,
                  "plafonne": pa["plafonne"], "mini": pa["mini"], **({"garder": True} if pa["garder"] else {}), "vendus": vendus.get(s, [])[-5:], "etat": c[0][1], **({"douteux": True} if douteux else {}),
                  "pf": {k: v for k, v in (("eb", ebay_vivantes.get(s)), ("vi", vinted_en_ligne.get(s)), ("lb", lbc_en_ligne.get(s))) if v}}
    SORTIE.write_text(json.dumps(out, ensure_ascii=False))
    pp = lambda s: float(ref.get(s, {}).get("prix_public") or 0) or None
    EXEMPLAIRES.write_text(json.dumps({s: {"pp": pp(s), "c": sorted(c, key=lambda x: (x[1], x[2]))} for s, c in ex.items()}, ensure_ascii=False))
    print(len(out), "sets en stock avec prix proposé")


if __name__ == "__main__":
    main()
