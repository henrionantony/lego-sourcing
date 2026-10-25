"""Plancher de prix calculé sur l'achat entier (la transaction), pas article par article.

Règle : la marge brute minimale (MARGE_BRUTE_PLANCHER) vaut pour tout l'achat. Un article qui traîne
peut descendre sous ce seuil tant que le reste de l'achat compense. Pour chaque achat :
  reste à faire = coût de l'achat × MARGE_BRUTE_PLANCHER − ce qui est déjà vendu,
réparti entre les exemplaires encore en stock au prorata de leur prix LEGO (un cadeau compte aussi).
Plancher d'un exemplaire = sa part, jamais au-dessus de son plancher seul (coût × marge minimale),
jamais sous son propre coût prélèvements payés (pas de vente à perte).
prix_achat d'un article = prix de la ligne entière (ta part s'il est co-investi), d'où le ÷ quantité.
"""
import csv
from pathlib import Path

import base
import regles

LEGO = Path(__file__).resolve().parent.parent


def _prix_lego() -> dict[str, float]:
    out = {}
    try:
        with open(LEGO / "data" / "referentiel.csv", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    out.setdefault(r["set_num"].split("-")[0], float(r.get("prix_public") or 0))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def planchers() -> dict[int, dict]:
    """{article_id: {plancher, seul, achat}} pour les articles encore en stock.
    plancher = prix minimal d'un exemplaire calculé sur l'achat ; seul = l'ancien plancher, article seul."""
    m = regles.MARGE_BRUTE_PLANCHER
    pp = _prix_lego()
    art = base.lire("articles", "select=id,achat_id,prix_achat,quantite,id_set,offert&limit=10000")
    ven = base.lire("ventes", "select=article_id,prix_vente&limit=20000")
    reste = {r["id"]: int(r.get("reste") or 0) for r in base.lire("stock", "select=id,reste&reste=gt.0&limit=10000")}
    vendu: dict[int, float] = {}
    for v in ven:
        vendu[v["article_id"]] = vendu.get(v["article_id"], 0) + float(v.get("prix_vente") or 0)
    achats: dict[int, list] = {}
    for a in art:
        achats.setdefault(a["achat_id"], []).append(a)
    out = {}
    for aid, lignes in achats.items():
        cout = sum(0 if a.get("offert") else float(a.get("prix_achat") or 0) for a in lignes)
        a_faire = max(0.0, cout * m - sum(vendu.get(a["id"], 0) for a in lignes))
        poids = {}
        for a in lignes:
            if not reste.get(a["id"]):
                continue
            unite = 0 if a.get("offert") else float(a.get("prix_achat") or 0) / max(1, int(a.get("quantite") or 1))
            s = str(a.get("id_set") or "").split("-")[0].removesuffix(".0")
            poids[a["id"]] = (pp.get(s) or unite * m or 1.0, unite)
        total = sum(w * reste[i] for i, (w, _) in poids.items()) or 1.0
        for i, (w, unite) in poids.items():
            part = a_faire * w / total
            # L'achat ne fait que relâcher le plancher quand il est en avance ; s'il est en retard
            # (autre article vendu sous le seuil), on garde le plancher de l'article seul.
            out[i] = {"plancher": round(max(min(part, unite * m), unite / (1 - regles.PRELEVEMENTS)), 2),
                      "seul": round(unite * m, 2), "achat": aid}
    return out


if __name__ == "__main__":
    p = planchers()
    plus_bas = [x for x in p.values() if x["plancher"] < x["seul"] - 0.5]
    print(len(p), "articles en stock ;", len(plus_bas), "peuvent descendre sous leur plancher seul grâce à leur achat")
