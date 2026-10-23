"""Textes d'annonces prêts à copier pour l'application (onglet Listings) :
les annonces Vinted en ligne ou masquées (export de l'extension), avec la
description sauvegardée dans Clemz quand elle existe, sinon un texte type.
La version Leboncoin (sans hashtags, avec la remise en main propre) est faite
par l'application.

Usage : python3 annonces.py   → ../stock/app/annonces.json
"""
import json
import re
from pathlib import Path

import pandas as pd

STOCK = Path(__file__).resolve().parent.parent / "stock"


def dernier(motif: str) -> Path | None:
    f = sorted(STOCK.glob(motif))
    return f[-1] if f else None


def texte_type(titre: str) -> str:
    """Description dans le style habituel quand Clemz n'en a pas."""
    m = re.match(r"\s*LEGO\s+(.+?)\s+-\s+(.+?)(?:\s+-\s+(\d{4,7}))?\s*$", titre, re.I)
    gamme, nom, num = (m.group(1), m.group(2), m.group(3)) if m else ("", titre, None)
    nom = re.sub(r"^(Set|Figurine|Minifig)\s+", "", nom, flags=re.I)
    tags = " ".join("#" + re.sub(r"\W", "", g.lower()) for g in ["lego", gamme] if g)
    return (f"🔔 Vends Lego {nom}{' - ' + num if num else ''}{' de la gamme Lego ' + gamme if gamme else ''}.\n\n"
            "✅ 💯 % Lego\n\nℹ️ Vous achetez ce que vous voyez en photo.\n\n"
            f"📦 Envoi rapide et soigné.\n\n{tags}")


def gamme(titre: str) -> str:
    """Gamme principale pour le filtre : « Ideas / BTS » → Ideas, séries de minifigs regroupées."""
    m = re.match(r"\s*LEGO\s+(.+?)\s+(?:-|–)\s", titre, re.I)
    g = m.group(1) if m else ""
    g = re.sub(r"\s+\d{4,7}$", "", g.split(" / ")[0]).strip()
    if not g or g.startswith(("-", "–")):
        return "Lots & parts" if re.search(r"\blot\b|pièces", titre, re.I) else "Merch & other"
    for motif, nom in [(r"^Minifigure", "Minifigures"), (r"^Promo", "Promotional"), (r"^City", "City"),
                       (r"^DC Comics", "DC Comics"), (r"^Marvel", "Marvel"), (r"^The LEGO Movie", "The LEGO Movie")]:
        if re.match(motif, g, re.I):
            return nom
    return g


def numeros(titre: str) -> list[str]:
    nums = re.findall(r"(?<!\w)(?<!\d[.,])(\d{4,7})(?:-\d)?(?!\w|[.,]\d)", titre)
    nums = [x for x in nums if not 1949 <= int(x) <= 2030] or nums   # « … - 1999 » = l'année, pas le set
    return [] if re.search(r"-\s*Figurine\b", titre) else nums


def numero(titre: str) -> str:
    n = numeros(titre)
    return n[-1] if n else ""


def construire() -> list[dict]:
    v = pd.read_csv(dernier("**/vinted_annonces_*.csv"), sep=";", dtype={"id": str}).fillna("")
    v = v[v.statut.isin(["en ligne", "masqué"])]
    c = dernier("**/clemz_articles_*.csv")
    textes = {}
    if c:
        cl = pd.read_csv(c, sep=";", dtype=str).fillna("")
        for _, r in cl.iterrows():
            m = re.search(r"/items/(\d+)", r["URL Vinted"])
            if m and r["Description"].strip():
                textes[m.group(1)] = r["Description"].strip()
    out = []
    for _, r in v.iterrows():
        desc = str(r.get("description") or "").strip() or textes.get(r.id) or texte_type(r.titre)
        # Numéro du set n'importe où dans le titre (« LEGO Ninjago 71834 – … », « … - 21037 - Rare ») : le dernier
        # nombre de 4 à 7 chiffres collé à aucune lettre ; jamais pour une figurine seule. Un lot garde tous ses numéros (ns)
        nums = numeros(r.titre)
        num = nums[-1] if nums else None
        out.append({"id": r.id, "t": r.titre, "p": float(r.prix or 0), "s": r.statut, "u": r.url,
                    "n": num or "", **({"ns": sorted(set(nums))} if len(set(nums)) > 1 else {}), "d": desc, "g": gamme(r.titre),
                    "type": not (str(r.get("description") or "").strip() or r.id in textes),
                    "ph": [p for p in str(r.get("photos") or "").split() if p],
                    "f": int(float(r.get("favoris") or 0))})
    prioriser(out)
    return out


def prioriser(annonces: list[dict]):
    """Priorité Leboncoin « pr » (0 à 5) : ce qui a le plus de chances de partir là-bas.
    Ventes historiques : les sets à 60 € et plus partent plus sur Leboncoin (≈54 %) que sur Vinted,
    les figurines bien moins (≈35 %). Bonus si l'annonce a des favoris (demande) ou traîne sur Vinted
    (ids Vinted croissants dans le temps : les plus petits sont les plus anciennes). Masquée = stock à vérifier."""
    ids = sorted(int(a["id"]) for a in annonces if a["id"].isdigit())
    seuil_ancien = ids[int(len(ids) * 0.4)] if ids else 0
    for a in annonces:
        set_ = bool(a["n"]) and not re.search(r"figurine|minifig|\bsw\d|mug|porte-cl", a["t"], re.I)
        p = a["p"]
        pr = (3 if p >= 60 else 2 if p >= 30 else 1) if set_ else (0.7 if p >= 15 else 0.3)
        pr += 1 if a["f"] >= 20 else 0.5 if a["f"] >= 12 else 0
        pr += 0.5 if a["id"].isdigit() and int(a["id"]) <= seuil_ancien else 0
        if a["s"] == "masqué":
            pr *= 0.6
        a["pr"] = round(min(pr, 5), 1)


if __name__ == "__main__":
    a = construire()
    f = STOCK / "app" / "annonces.json"
    f.write_text(json.dumps(a, ensure_ascii=False))
    print(f"{len(a)} annonces, {sum(not x['type'] for x in a)} avec ta description, "
          f"{sum(bool(x['ph']) for x in a)} avec photos → {f}")
