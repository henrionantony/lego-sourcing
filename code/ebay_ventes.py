"""Annonces eBay dont l'objet est déjà vendu ailleurs (Vinted, Leboncoin, en main propre…).

Une figurine ou un set vendu sur une autre plateforme doit disparaître d'eBay tout de suite,
sinon on risque de vendre deux fois le même objet.
- Figurine (annonce « fig-<code> ») : une vente hors eBay du même code depuis la mise en ligne
  retire l'annonce seulement s'il n'en reste plus en stock (table figurines). S'il en reste,
  la quantité de l'annonce est baissée au stock restant (a_baisser, 13/11).
- Set : une vente hors eBay du set depuis la mise en ligne ET plus aucun exemplaire en stock.

Utilisé par ebay_annonces.py (retrait sur eBay) et par le passage horaire :
  python3 ebay_ventes.py   → affiche les annonces à retirer (rien = rien à faire)
"""
import re

import base
from figs_sets import cle


def stock_figs() -> dict[str, list[int]]:
    """Par code de figurine (normalisé) : [exemplaires connus, exemplaires pas encore vendus]."""
    out: dict[str, list[int]] = {}
    for f in base.lire("figurines", "select=ref,vente_id&limit=5000") or []:
        n = out.setdefault(cle(str(f.get("ref") or "")), [0, 0])
        n[0] += 1
        n[1] += 0 if f.get("vente_id") else 1
    return out


def a_baisser(faites: dict | None = None) -> dict[str, int]:
    """Annonces de figurines en ligne qui affichent plus d'exemplaires qu'il n'en reste : nouvelle quantité."""
    faites = faites if faites is not None else (base.doc("ebay/annonces") or {})
    st = stock_figs()
    out = {}
    for k, v in faites.items():
        if k.startswith("fig-") and v.get("listing") and not v.get("retire"):
            n = st.get(cle(k[4:]))
            if n and 0 < n[1] < int(v.get("qte") or 1):
                out[k] = n[1]
    return out


def a_retirer(faites: dict | None = None) -> dict[str, str]:
    faites = faites if faites is not None else (base.doc("ebay/annonces") or {})
    vivantes = {k: v for k, v in faites.items() if v.get("listing") and not v.get("retire")}
    if not vivantes:
        return {}
    depuis = min(str(v.get("cree_le") or v.get("le") or "2026-01-01")[:10] for v in vivantes.values())
    ventes = [x for x in base.lire("ventes", f"select=id,date_vente,plateforme,id_minifig,nom_article,produit&date_vente=gte.{depuis}&limit=5000")
              if "EBAY" not in str(x.get("plateforme") or "").upper()]
    stock = None
    figs = None
    out = {}
    for k, v in vivantes.items():
        le = str(v.get("cree_le") or v.get("le") or "")[:10]
        if k.startswith("fig-"):
            c = cle(k[4:])
            for x in ventes:
                if x.get("id_minifig") and cle(x["id_minifig"]) == c and str(x["date_vente"]) >= le:
                    if figs is None:
                        figs = stock_figs()
                    n = figs.get(c)
                    if n and n[1] > 0:   # il en reste : on baisse la quantité (a_baisser), on ne retire pas
                        break
                    out[k] = f"figurine vendue {x['plateforme']} le {x['date_vente']} (vente {x['id']})"
                    break
        elif k.split("-")[0].isdigit():
            s = k.split("-")[0]
            vendu = [x for x in ventes if str(x["date_vente"]) >= le and not x.get("id_minifig")
                     and re.search(rf"(?<!\d){s}(?!\d)", str(x.get("nom_article") or ""))
                     and str(x.get("produit") or "SET").upper() == "SET"]
            if vendu:
                if stock is None:
                    stock = base.stock_par_set()
                if stock.get(s, 0) <= 0:
                    out[k] = f"set vendu {vendu[0]['plateforme']} le {vendu[0]['date_vente']}, plus en stock"
    return out


def noter(cles) -> None:
    """Figurines retirées : marquées vendues dans les réglages, pour ne jamais les republier."""
    r = base.doc("ebay/reglages") or {}
    change = False
    for f in r.get("figurines", []):
        if f"fig-{f.get('code')}" in cles and not f.get("vendue"):
            f["vendue"] = True
            change = True
    if change:
        base.doc_ecrire("ebay/reglages", r)


if __name__ == "__main__":
    for k, raison in a_retirer().items():
        print(k, ":", raison)
    for k, q in a_baisser().items():
        print(k, f": quantité à baisser à {q} (vendue ailleurs, il en reste)")
