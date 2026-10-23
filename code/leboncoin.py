"""Annonces Leboncoin (export de l'extension, bouton orange) reliées aux annonces Vinted.

Vinted reste la référence : pour chaque annonce Vinted, on cherche l'annonce Leboncoin
du même set (numéro de set dans le titre). Une annonce Leboncoin ne sert qu'une fois,
donc deux exemplaires sur Vinted pour une seule annonce Leboncoin = un seul « posté ».

Usage : python3 leboncoin.py [export.csv]  → ../stock/app/leboncoin.json
Sans argument : le plus récent de ../stock/leboncoin/.
"""
import csv
import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
APP = RACINE / "stock" / "app"
NUM = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")


def titre(texte: str) -> str:
    """La carte Leboncoin répète le titre trois fois : on garde la première."""
    fin = texte.find(" " + texte[:30], 10)
    return (texte[:fin] if fin > 0 else texte.split(" Prix:")[0]).strip()


def lire(chemin: Path) -> list[dict]:
    lignes = []
    with open(chemin, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            t = titre(r.get("texte") or r["titre"])
            prix = re.search(r"(?:Prix:|^)\s*([\d\s ]+(?:,\d+)?)\s*€", (r.get("texte") or "").replace(t, "").strip())
            lignes.append({"id": r["id"], "t": t, "u": r["url"],
                           "p": float(prix.group(1).replace(" ", "").replace(" ", "").replace(",", ".")) if prix else None,
                           "sets": NUM.findall(t)})
    return lignes


def mots(t: str) -> set:
    return {m for m in re.findall(r"[a-zà-ÿ0-9]{3,}", t.lower()) if m not in {"lego", "set", "neuf", "scellé", "scelle"}}


def relier(vinted: list[dict], lbc: list[dict]) -> dict:
    """{id Vinted: id Leboncoin}"""
    paires = []
    for v in vinted:
        nums = set(NUM.findall(v["t"])) | ({v["n"]} if v.get("n") else set())
        if v["s"] not in ("en ligne", "masqué") or not nums:
            continue
        for l in lbc:
            if nums & set(l["sets"]):
                score = len(mots(v["t"]) & mots(l["t"])) + (3 if l["p"] == v["p"] else 0) + (1 if v["s"] == "en ligne" else 0)
                paires.append((score, v["id"], l["id"]))
    lien, pris = {}, set()
    for _, vid, lid in sorted(paires, reverse=True):
        if vid not in lien and lid not in pris:
            lien[vid] = lid
            pris.add(lid)
    return lien


def prevenir(vendus: list[dict]):
    """Un message Telegram par annonce Leboncoin dont le set est vendu sur Vinted (une seule fois)."""
    deja_f = RACINE / "data" / "cache" / "lbc_alertees.json"
    deja = set(json.loads(deja_f.read_text())) if deja_f.exists() else set()
    nouveaux = [l for l in vendus if l["id"] not in deja]
    if not nouveaux:
        return
    sys.path.insert(0, str(Path(__file__).parent))
    import alertes
    texte = "⚠️ Vendu sur Vinted, encore en ligne sur Leboncoin : supprime l'annonce (sauf s'il t'en reste un autre)\n\n" + \
        "\n\n".join(f"{l['t']}\n{l['u']}" for l in nouveaux)
    try:
        alertes.telegram(texte, True, "--essai" in sys.argv)
        deja_f.parent.mkdir(parents=True, exist_ok=True)
        deja_f.write_text(json.dumps(sorted(deja | {l["id"] for l in nouveaux})))
    except Exception as e:
        print(f"Telegram : {e}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chemin = Path(args[0]) if args else max((RACINE / "stock" / "leboncoin").glob("leboncoin_annonces_*.csv"))
    lbc = lire(chemin)
    vinted = json.loads((APP / "annonces.json").read_text())
    lien = relier(vinted, lbc)
    seules = [l for l in lbc if l["id"] not in set(lien.values())]
    # Annonces Leboncoin dont le set est déjà vendu sur Vinted : à supprimer (risque de double vente).
    export = max((RACINE / "stock" / "vinted").glob("vinted_annonces_*.csv"))
    with open(export, encoding="utf-8-sig") as f:
        vendus = {n for r in csv.DictReader(f, delimiter=";") if r["statut"] == "vendu" for n in NUM.findall(r["titre"])}
    # … mais seulement s'il ne t'en reste plus aucun dans la base : sinon l'annonce Leboncoin vend un autre exemplaire
    try:
        ex = json.loads((APP / "exemplaires.json").read_text())
    except (OSError, ValueError):
        ex = {}
    reste = lambda s: sum(c[4] for c in (ex.get(s) or {}).get("c", []))
    for l in seules:
        l["vendu"] = any(s in vendus and not reste(s) for s in l["sets"])
    (APP / "leboncoin.json").write_text(json.dumps({
        "export": chemin.name, "date": (re.search(r"\d{4}-\d{2}-\d{2}", chemin.name) or [""])[0], "lien": {v: next(l["u"] for l in lbc if l["id"] == i) for v, i in lien.items()},
        "seules": [{k: l[k] for k in ("id", "t", "p", "u", "vendu")} for l in seules]}, ensure_ascii=False))
    prevenir([l for l in seules if l["vendu"]])
    print(f"{len(lbc)} annonces Leboncoin, {len(lien)} reliées à Vinted, {len(seules)} sans annonce Vinted")
    for l in seules:
        print("  VENDU sur Vinted :" if l["vendu"] else "  pas sur Vinted :", l["t"], l["p"])


if __name__ == "__main__":
    main()
