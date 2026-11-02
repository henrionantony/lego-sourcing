"""Veille Dealabs sur des achats hors LEGO demandés (ex. imprimante thermique,
02/11/2026) : un message Telegram par nouveau deal en cours qui colle aux critères.

Usage : python3 veille_dealabs.py [--essai]
Les veilles sont dans data/veilles.json : [{"nom", "recherches": [...], "mots": [...],
"exclus": [...], "prix_max", "jusqu": "AAAA-MM-JJ"}]. Deals déjà envoyés : data/cache/veille_vus.json.
"""
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import alertes

DATA = Path(__file__).resolve().parent.parent / "data"
VEILLES = DATA / "veilles.json"
VUS = DATA / "cache" / "veille_vus.json"


def chercher(q: str) -> list[dict]:
    url = "https://www.dealabs.com/search?" + urllib.parse.urlencode({"q": q, "sortBy": "new"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    s = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    deals = []
    for part in s.split('"threadId":')[1:]:
        i = re.match(r'"?(\d+)', part)
        t = re.search(r'"title":"((?:[^"\\]|\\.)+)"', part)
        if not (i and t):
            continue
        e = re.search(r'"isExpired":(true|false)', part)
        p = re.search(r'"price":([0-9.]+)', part)
        sl = re.search(r'"titleSlug":"([^"]+)"', part)
        deals.append({"id": i.group(1), "titre": json.loads('"' + t.group(1) + '"'),
                      "expire": bool(e and e.group(1) == "true"), "prix": float(p.group(1)) if p else None,
                      "slug": sl.group(1) if sl else ""})
    return deals


def main(essai=False):
    if not VEILLES.exists():
        return
    vus = set(json.loads(VUS.read_text())) if VUS.exists() else set()
    aujourd = dt.date.today().isoformat()
    for v in json.loads(VEILLES.read_text()):
        if v.get("jusqu") and aujourd > v["jusqu"]:
            continue
        trouves = {}
        for q in v["recherches"]:
            try:
                for d in chercher(q):
                    trouves[d["id"]] = d
            except Exception as e:
                print("veille", q, e)
        for d in trouves.values():
            t = d["titre"].lower()
            if d["expire"] or d["id"] in vus:
                continue
            if not any(m in t for m in v["mots"]) or any(x in t for x in v.get("exclus", [])):
                continue
            if v.get("prix_max") and (d["prix"] is None or d["prix"] > v["prix_max"]):
                continue
            prix = f"{d['prix']:.2f} €".replace(".", ",") if d["prix"] is not None else "prix ?"
            alertes.telegram(f"🖨️ VEILLE {v['nom']} : {d['titre']}\n{prix}\n{alertes.lien_dealabs(d['id'], d['slug'])}",
                             sonne=True, essai=essai)
            print("envoyé", d["id"], d["titre"], prix)
            if not essai:
                vus.add(d["id"])
    if not essai:
        VUS.write_text(json.dumps(sorted(vus)))


if __name__ == "__main__":
    main("--essai" in sys.argv)
