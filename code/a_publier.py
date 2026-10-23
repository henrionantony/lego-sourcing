"""Prix proposé pour les sets en stock qui ne sont publiés nulle part.

Prix = le plus haut entre l'estimation de revente (referentiel.csv) et le
plancher du lot (coût d'achat × MARGE_BRUTE_PLANCHER, part d'associé comprise).
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

ICI = Path(__file__).resolve().parent
LEGO = ICI.parent
SORTIE = LEGO / "stock" / "app" / "a_publier.json"
# Exemplaires en stock par set, le moins cher d'abord, pour rattacher une vente scannée
EXEMPLAIRES = LEGO / "stock" / "app" / "exemplaires.json"


def arrondi(p: float) -> float:
    """Prix d'annonce rond : au 5 € au-dessus dès 30 €, à l'euro en dessous."""
    return float(math.ceil(p / 5) * 5) if p >= 30 else float(math.ceil(p))


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
    couts: dict[str, list] = {}
    ex: dict[str, list] = {}
    for l in stock:
        s = str(l["id_set"]).split("-")[0].removesuffix(".0")
        part = float(l.get("quote_part") or 1) or 1
        # prix_achat = prix de la ligne entière : on le ramène à un exemplaire
        cout = 0.0 if l.get("offert") else float(l["prix_achat"] or 0) / part / max(1, int(l.get("quantite") or 1))
        couts.setdefault(s, []).append((cout, l.get("details_etat") or ""))
        ex.setdefault(s, []).append([l["id"], round(cout, 2), l.get("date_achat") or "", l.get("lieu") or "", int(l.get("reste") or 1), *hz.get(l["id"], ("", ""))])
    out = {}
    for s, c in couts.items():
        r = ref.get(s, {})
        estime = float(r.get("pv_rapide") or r.get("prix_vente_estime") or 0)
        if r.get("statut") == "retiré" and r.get("pv_garde"):
            estime = max(estime, float(r["pv_garde"]))
        cout = min(x[0] for x in c)
        plancher = cout * regles.MARGE_BRUTE_PLANCHER
        # Coût bien au-dessus de l'estimation : souvent le prix d'un lot entier
        # posé sur un seul article. On garde l'estimation et on le signale.
        douteux = bool(estime) and cout > 2 * estime
        # Départ au-dessus de l'estimation, puis la liste du lundi baisse de 5 % tous les 30 jours jusqu'au plancher
        prix = estime if douteux else max(estime * regles.PRIX_DEPART, plancher, regles.GWP_DEFAUT if cout == 0 and not estime else 0)
        prix = arrondi(prix) if prix else None
        # Encore au catalogue : prix + frais acheteur + port sous le prix LEGO, jamais sous le plancher
        cap = regles.plafond_catalogue(float(r.get("prix_public") or 0)) if r.get("statut") == "en vente" else None
        if prix and cap and prix > cap:
            prix = max(cap, float(math.ceil(plancher)))
        # Pour choisir « poster maintenant » ou « garder » : revente rapide, revente après retraite, date de retraite
        conseil = {"be": (be.get(s + "-1") or {}).get("date") if (be.get(s + "-1") or {}).get("valeur_neuf") else None,
                   "src": r.get("fiabilite") or "",
                   "st": r.get("statut") or "", "rapide": float(r.get("pv_rapide") or 0) or None,
                   "garde": float(r.get("pv_garde") or 0) or None, "retrait": r.get("retrait_croise") or r.get("retrait") or ""}
        out[s] = {"prix": prix, "estime": round(estime, 2) or None, **conseil,
                  "cout": round(cout, 2), "etat": c[0][1], **({"douteux": True} if douteux else {})}
    SORTIE.write_text(json.dumps(out, ensure_ascii=False))
    pp = lambda s: float(ref.get(s, {}).get("prix_public") or 0) or None
    EXEMPLAIRES.write_text(json.dumps({s: {"pp": pp(s), "c": sorted(c, key=lambda x: (x[1], x[2]))} for s, c in ex.items()}, ensure_ascii=False))
    print(len(out), "sets en stock avec prix proposé")


if __name__ == "__main__":
    main()
