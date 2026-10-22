"""Historique récent des deals Lego Dealabs (pages du groupe « lego », environ
3 mois). Sert de signal de demande France par set et par gamme.

Usage : python3 dealabs_groupe.py  → ../data/dealabs_deals.csv (cumulé à chaque
lancement, pour garder les deals au-delà des 3 mois affichés).
"""
import json
import re
import time
import urllib.error
import urllib.request

import pandas as pd

from dealabs import UA, PAUSE, CACHE

RE_THREAD = re.compile(r'"thread":(\{"threadId".*?"isSuperHot")', re.S)
# Numéro de set : 4 à 6 chiffres isolés dans le titre (le premier trouvé).
RE_SET = r"(?<![\d,.])(\d{4,6})(?![\d,.€%]|\s?(?:€|pi[eè]ces|pcs))"


def extraire_set(titre: str):
    m = re.search(RE_SET, titre)
    return m.group(1) if m else None


def champ(bloc, nom, motif=r'(-?[\d.]+)'):
    m = re.search(rf'"{nom}":{motif}', bloc)
    return m.group(1) if m else None


if __name__ == "__main__":
    lignes, page = [], 1
    while True:
        req = urllib.request.Request(f"https://www.dealabs.com/groupe/lego?page={page}",
                                     headers={"User-Agent": UA, "Accept-Language": "fr-FR"})
        try:
            h = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 410:  # au-delà de la dernière page
                break
            raise
        blocs = RE_THREAD.findall(h)
        if not blocs:
            break
        for b in blocs:
            titre = json.loads('"' + champ(b, "title", r'"((?:[^"\\]|\\.)*)"') + '"')
            lignes.append({"id": champ(b, "threadId", r'"?(\d+)"?'), "titre": titre,
                           "temperature": float(champ(b, "temperature") or 0),
                           "commentaires": int(champ(b, "commentCount", r'(\d+)') or 0),
                           "prix": champ(b, "price"), "publie": int(champ(b, "publishedAt", r'(\d+)'))})
        page += 1
        time.sleep(PAUSE)

    d = pd.DataFrame(lignes).drop_duplicates("id")
    d["publie"] = pd.to_datetime(d.publie, unit="s")
    d["set"] = d.titre.str.extract(RE_SET)[0]
    # Fusion avec les relevés précédents : on garde la dernière température vue.
    f = CACHE.parent.parent / "dealabs_deals.csv"
    if f.exists():
        ancien = pd.read_csv(f, dtype={"id": str, "set": str}, parse_dates=["publie"])
        d = pd.concat([ancien[~ancien.id.isin(d.id)], d], ignore_index=True)
    d.to_csv(f, index=False)
    print(f"{len(d)} deals sur {page - 1} pages, du {d.publie.min():%d/%m/%Y} au {d.publie.max():%d/%m/%Y}, "
          f"{d.set.notna().sum()} avec numéro de set")
