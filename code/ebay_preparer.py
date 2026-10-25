"""Prépare les annonces eBay des derniers sets reçus (stock sûr) dans la base privée.

Pour chaque set : titre (80 caractères max), description, prix eBay (regles.prix_ebay du prix
d'annonce), quantité reçue, port France / UE + Suisse / Royaume-Uni (port.py), photo produit
officielle, EAN, gamme, pièces, année. Écrit le document « ebay/a_publier » ; ebay_annonces.py
(GitHub) les publie et note l'annonce dans « ebay/annonces ».

Usage : python3 ebay_preparer.py [set …]   (sans argument : articles reçus encore « à traiter »)
"""
import glob
import gzip
import html
import json
import sys
import urllib.parse
from pathlib import Path

import base
import port
import regles

ICI = Path(__file__).resolve().parent
APP = ICI.parent / "stock" / "app"


def brickset() -> dict:
    out = {}
    for f in glob.glob(str(ICI.parent / "data" / "cache" / "brickset_*.json.gz")):
        for s in json.load(gzip.open(f)):
            out[str(s.get("number"))] = s
    return out


def derniers_recus() -> dict[str, int]:
    """Quantité par set des articles neufs scellés reçus et pas encore mis en vente."""
    q = {}
    for a in base.lire("articles", "select=id_set,quantite,co_investisseur&categorie=eq.SET&livre=eq.true"
                                   "&statut=eq." + urllib.parse.quote("à traiter") + "&details_etat=eq.SCELLE&limit=1000"):
        if a.get("id_set") and not a.get("co_investisseur"):
            s = str(a["id_set"])
            q[s] = q.get(s, 0) + int(a.get("quantite") or 1)
    return q


def description(s, en, fr, b) -> str:
    nom = f"{fr} ({en})" if fr and fr.lower() != en.lower() else en
    pieces, annee, gamme = b.get("pieces"), b.get("year"), b.get("theme")
    lignes = [f"<h2>LEGO {html.escape(gamme or '')} {s} – {html.escape(nom)}</h2>",
              "<p><b>Neuf, boîte scellée</b> (jamais ouverte).</p><ul>",
              f"<li>Numéro du set : {s}</li>"]
    if gamme: lignes.append(f"<li>Gamme : {html.escape(gamme)}</li>")
    if pieces and str(pieces) != "0": lignes.append(f"<li>Pièces : {pieces}</li>")
    if annee: lignes.append(f"<li>Année : {annee}</li>")
    lignes += ["</ul><p>Photo du produit officielle : le set envoyé est neuf et scellé, boîte en bon état.</p>",
               "<p>📦 Envoi soigné en carton avec calage, en Colissimo suivi depuis la France.</p>",
               "<p>🇪🇺 Envoi dans toute l'Union européenne, en Suisse et au Royaume-Uni.</p>",
               "<p>Vendeur particulier passionné, plus de 500 avis positifs sur d'autres plateformes.</p>"]
    return "".join(lignes)


def main():
    ap = json.loads((APP / "a_publier.json").read_text())
    noms = json.loads((APP / "noms_fr.json").read_text())
    bs = brickset()
    deja = base.doc("ebay/annonces") or {}
    voulus = {s: None for s in sys.argv[1:]} if len(sys.argv) > 1 else derniers_recus()
    out, sans = {}, []
    for s, q in voulus.items():
        v, b = ap.get(s) or {}, bs.get(s) or {}
        if s in deja and deja[s].get("listing"):
            continue
        if not v.get("prix") or not b:
            sans.append(s)
            continue
        en, fr = b.get("name") or "", (noms.get(s) or {}).get("nom") or ""
        t = f"LEGO {s} {en}" + (f" / {fr}" if fr and fr.lower() != en.lower() else "") + " - Neuf scellé"
        if len(t) > 80:
            t = f"LEGO {b.get('theme') or ''} {s} {en} - Neuf scellé".replace("  ", " ")
        if len(t) > 80:
            t = f"LEGO {s} {en} - Neuf scellé"[:80]
        p = port.port(s, b.get("pieces"))
        if not p or not p["fr"] or not p["ue"] or p["hors_norme"]:
            sans.append(s)
            continue
        img = (b.get("image") or {}).get("imageURL")
        out[s] = {"titre": t, "description": description(s, en, fr, b), "prix": regles.prix_ebay(v["prix"]),
                  "qte": q or 1, "port": {k: p[k] for k in ("fr", "ue", "uk", "kg")}, "images": [img] if img else [],
                  "ean": (b.get("barcode") or {}).get("EAN"), "gamme": b.get("theme"), "pieces": b.get("pieces"),
                  "annee": b.get("year"), "nom": en}
    if not base.doc_ecrire("ebay/a_publier", out):
        sys.exit("écriture refusée")
    print(f"{len(out)} annonces prêtes, {len(sans)} sets sans prix ou port : {' '.join(sans)}")


if __name__ == "__main__":
    main()
