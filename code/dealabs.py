"""Signal de demande France : deals Dealabs passés d'un set (température,
commentaires, prix). Une recherche par set, une requête toutes les 3 s.

Usage : python3 dealabs.py 10312 75365 ...  → ajoute à ../data/cache/dealabs.json
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "dealabs.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
PAUSE = 3.0
# Chaque deal de la page de recherche est un objet JSON « thread » ; on en
# extrait les champs utiles sans dépendre du reste de la page.
RE_DEAL = re.compile(r'"threadId":"?(\d+)"?.{0,400}?"title":"((?:[^"\\]|\\.)*)"(.{0,3000}?)"temperature":(-?[\d.]+)', re.S)
RE_PRIX = re.compile(r'"price":(\d+(?:\.\d+)?)')
RE_DATE = re.compile(r'"publishedAt":(\d+)')
RE_COMM = re.compile(r'"commentCount":(\d+)')


def chercher(numero: str) -> list[dict]:
    url = "https://www.dealabs.com/search?q=" + urllib.parse.quote(f"lego {numero}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR"})
    with urllib.request.urlopen(req, timeout=30) as r:
        h = r.read().decode("utf-8", "replace")
    deals, vus = [], set()
    for m in RE_DEAL.finditer(h):
        tid, titre, milieu, temp = m.groups()
        titre = json.loads(f'"{titre}"')
        # La recherche est floue : on ne garde que les deals qui citent le set.
        if tid in vus or not re.search(rf"(?<!\d){re.escape(numero)}(?!\d)", titre):
            continue
        vus.add(tid)
        p, d, c = RE_PRIX.search(milieu), RE_DATE.search(milieu), RE_COMM.search(milieu)
        deals.append({"id": tid, "titre": titre, "temperature": float(temp),
                      "prix": float(p.group(1)) if p else None,
                      "date": int(d.group(1)) if d else None,
                      "commentaires": int(c.group(1)) if c else None})
    return deals


def collecter(numeros):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for n in numeros:
        if n in cache:
            continue
        try:
            cache[n] = chercher(n)
        except Exception as e:
            print(f"Dealabs {n} : {e}", file=sys.stderr)
            continue
        CACHE.write_text(json.dumps(cache, ensure_ascii=False))
        time.sleep(PAUSE)
    return cache


if __name__ == "__main__":
    c = collecter(sys.argv[1:])
    for n in sys.argv[1:]:
        print(n, len(c.get(n, [])), "deals")
