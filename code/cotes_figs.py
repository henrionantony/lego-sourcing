"""Cotes BrickEconomy des figurines, sur la réserve d'appels quotidienne des figurines.

Ordre : figurines en stock (table figurines), puis les plus vendues (ventes.id_minifig),
puis celles des sets relevés par BrickEconomy (scénario découpe des alertes).
Cache disque 30 jours (data/cache/brickeconomy_figurines.json) ; si les tables
figurines_be / figurines_sets / cotes existent dans Supabase, elles sont remplies.
Usage : python3 cotes_figs.py [--coef]   (--coef : affiche le coefficient de vente)
"""
import collections
import datetime as dt
import json
import sys

import base
from referentiel import CACHE, BrickEconomy

FIGS = CACHE / "brickeconomy_figurines.json"


def codes_prioritaires(be: BrickEconomy) -> list[str]:
    stock = [str(f.get("ref") or "").lower() for f in base.lire("figurines", "select=ref&limit=10000")]
    vendues = collections.Counter(str(v["id_minifig"]).lower() for v in
                                  base.lire("ventes", "select=id_minifig&id_minifig=not.is.null&limit=20000"))
    sets = [n for c in be.cache.values() for n in ((c.get("figurines") or {}).get("minifigs_quantities") or {})]
    vus, out = set(), []
    for c in stock + [c for c, _ in vendues.most_common()] + sets:
        if c and c not in vus:
            vus.add(c)
            out.append(c)
    return out


def coefficient() -> dict[str, tuple[float, int]]:
    """Prix de vente de tes figurines / cote BrickEconomy actuelle, par gamme
    (somme sur somme, ventes partagées remises à 100 %)."""
    figs = json.loads(FIGS.read_text()) if FIGS.exists() else {}
    s: dict[str, list] = {}
    for v in base.lire("ventes", "select=id_minifig,prix_vente,co_investisseur&id_minifig=not.is.null&limit=20000"):
        c = figs.get(str(v["id_minifig"]).lower()) or {}
        if not c.get("valeur_neuf"):
            continue
        pv = float(v["prix_vente"]) * (2 if v.get("co_investisseur") else 1)
        for g in (c.get("gamme") or "?", "TOUTES"):
            t = s.setdefault(g, [0.0, 0.0, 0])
            t[0] += pv
            t[1] += float(c["valeur_neuf"])
            t[2] += 1
    return {g: (round(a / b, 2), n) for g, (a, b, n) in s.items() if b}


def main(be: BrickEconomy | None = None):
    be = be or BrickEconomy()
    codes = codes_prioritaires(be)
    for c in codes:
        if be.restant(figurines=True) <= 0:
            break
        be.figurine(c)
    figs = json.loads(FIGS.read_text()) if FIGS.exists() else {}
    jour = dt.date.today().isoformat()
    ref = [{"code": k, "nom": v.get("nom"), "gamme": v.get("gamme"), "valeur_neuf": v.get("valeur_neuf"),
            "maj": v["date"]} for k, v in figs.items() if not v.get("inconnue")]
    # Tables créées dans Supabase ? (sinon le cache disque suffit)
    if ref and base._appel("GET", "figurines_be?select=code&limit=1") is not None:
        base.ajouter("figurines_be", ref, fusion=True)
        base.ajouter("figurines_sets", [{"code": k, "id_set": s} for k, v in figs.items() for s in v.get("sets") or []],
                     fusion=True)
        base.ajouter("cotes", [{"code": k, "le": v["date"], "valeur_neuf": v.get("valeur_neuf")}
                               for k, v in figs.items() if v["date"] == jour], fusion=True)
    print(f"{len(figs)} figurines cotées ({sum(1 for v in figs.values() if v['date'] == jour)} aujourd'hui), "
          f"{len(codes)} à suivre, appels restants {be.restant(figurines=True)}")
    if "--coef" in sys.argv:
        for g, (r, n) in sorted(coefficient().items(), key=lambda kv: -kv[1][1]):
            print(f"  {g} : × {r} sur {n} ventes")


if __name__ == "__main__":
    main()
