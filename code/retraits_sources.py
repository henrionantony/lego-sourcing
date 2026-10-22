"""Dates de retrait croisées entre plusieurs sources, pour estimer la
fiabilité de chaque annonce (Lego peut prolonger un set au dernier moment).

Sources :
- Brickset : date de fin (API, dans le référentiel) ;
- StoneWars : liste annuelle des retraits (page « retiring-AAAA », mise à jour
  irrégulièrement : à égalité, Brickset l'emporte) ;
- Hoth Bricks : articles « bientôt retirés », dont les listes envoyées par Lego ;
- Brick Fanatics : articles sur les retraits lus par le flux RSS (le site
  bloque la lecture directe des pages ; le flux est cumulé jour après jour).
Lego.com bloque les lectures automatiques : sa page « bientôt retirés » n'est
connue qu'à travers Hoth Bricks.

Usage : python3 retraits_sources.py  → ../data/retraits_sources.csv
"""
import datetime as dt
import html
import json
import re
import urllib.request

import pandas as pd

from referentiel import CACHE, DATA, charger

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
AN = dt.date.today().year
CUMUL = CACHE / "retraits_articles.json"


def lire(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,en"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def texte(h: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "|", h))
    return re.sub(r"(\|\s*)+", "|", re.sub(r"\s+", " ", t))


def vague(t: str) -> str | None:
    """Vague de retrait évoquée par un article : 31/12 ou 31/07 de l'année."""
    t = t.lower()
    if re.search(r"fin d.ann[ée]e|31 d[ée]cembre|end of (the )?year|end of %d|december|year.end" % AN, t):
        return f"{AN}-12-31"
    if re.search(r"juillet|july|mid.year|mi.ann[ée]e", t):
        return f"{AN}-07-31"
    return None


def stonewars() -> dict:
    t = texte(lire(f"https://stonewars.com/retiring-{AN}/"))
    out = {}
    for num, fin in re.findall(r"\|(\d{4,6})\|[^|]+\|\d{4}, \d{2}\|(\d{4}, \d{2})\|", t):
        a, m = map(int, fin.split(", "))
        out[num] = (pd.Timestamp(a, m, 1) + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
    return out


def articles_rss(url: str, source: str, cumul: dict):
    """Ajoute au cumul les articles du flux qui parlent de retraits."""
    x = lire(url)
    for item in re.findall(r"<item>(.*?)</item>", x, re.S):
        titre = html.unescape(re.search(r"<title>(.*?)</title>", item, re.S).group(1))
        lien = re.search(r"<link>(.*?)</link>", item, re.S).group(1).strip()
        date = pd.Timestamp(re.search(r"<pubDate>(.*?)</pubDate>", item, re.S).group(1)).strftime("%Y-%m-%d")
        if not re.search(r"retir|fin de (vie|carri)", titre, re.I) or not date.startswith(str(AN)):
            continue
        corps = re.search(r"<content:encoded>(.*?)</content:encoded>", item, re.S)
        corps = texte(corps.group(1)) if corps else ""
        if not corps and source == "Hoth Bricks":
            h = lire(lien)
            corps = texte(h[h.find("<article"):h.find("</article>")])
        cumul[lien] = {"source": source, "titre": titre, "date": date,
                       "vague": vague(titre + " " + corps[:600]),
                       "officiel": bool(re.search(r"LEGO a envoy|liste que LEGO|official", corps, re.I)),
                       "sets": sorted(set(re.findall(r"(?<!\d)(\d{5})(?!\d)", corps)))}


def construire() -> pd.DataFrame:
    cumul = json.loads(CUMUL.read_text()) if CUMUL.exists() else {}
    for url, src in ((f"https://www.hothbricks.com/search/retir%C3%A9s/feed/rss2/", "Hoth Bricks"),
                     (f"https://www.hothbricks.com/search/retraits/feed/rss2/", "Hoth Bricks"),
                     ("https://www.brickfanatics.com/feed", "Brick Fanatics")):
        try:
            articles_rss(url, src, cumul)
        except Exception as e:
            print(f"{src} : {e}")
    CUMUL.write_text(json.dumps(cumul, ensure_ascii=False, indent=1))

    annonces = []  # (set, source, date, officiel)
    try:
        annonces += [(s, "StoneWars", d, False) for s, d in stonewars().items()]
    except Exception as e:
        print(f"StoneWars : {e}")
    for a in cumul.values():
        if a["vague"]:
            annonces += [(s, a["source"], a["vague"], a["officiel"]) for s in a["sets"]]
    ref = charger()
    auj = dt.date.today().isoformat()
    ref = ref[ref.retrait.notna() & (ref.retrait >= auj) & ref.retrait.str.startswith((str(AN), str(AN + 1)))]
    annonces += [(b, "Brickset", d, False) for b, d in zip(ref.base, ref.retrait)]
    # Page Lego.com « Bientôt retirés » (copiée par l'utilisateur, voir lego_officiel.py) :
    # pas de date affichée, on prend celle de Brickset si elle tombe avant fin d'année, sinon le 31/12.
    off = DATA / "lego_officiel.csv"
    if off.exists():
        bs = dict(zip(ref.base, ref.retrait))
        fin = f"{AN}-12-31"
        annonces += [(s, "Lego.com", bs[s] if bs.get(s, "9") <= fin else fin, True)
                     for s in pd.read_csv(off, dtype={"set": str}).set if len(s) == 5]
    a = pd.DataFrame(annonces, columns=["set", "source", "date", "officiel"]).drop_duplicates(["set", "source"])
    # Ne garder que les vrais numéros de sets connus du catalogue.
    a = a[a.set.isin(set(charger().base)) & (a.date >= auj)]
    lignes = []
    for s, g in a.groupby("set"):
        # Date retenue : la plus soutenue ; à égalité, la liste Lego puis Brickset
        # (mis à jour en continu) l'emportent sur les listes publiées plus tôt.
        poids = g.source.map({"Brickset": 1.5}).fillna(1.0) + g.officiel * 3
        date = poids.groupby(g.date).sum().idxmax()
        d_accord = g[g.date == date]
        n = d_accord.source.nunique()
        officiel = bool(g.officiel.any())
        niveau = "très probable" if officiel or n >= 3 else "probable" if n == 2 else "à confirmer"
        lignes.append({"set": s, "retrait_croise": date, "sources_daccord": n,
                       "sources": ", ".join(sorted(d_accord.source.unique())) + (" (liste Lego)" if officiel else ""),
                       "desaccord": ", ".join(f"{x.source} {x.date}" for x in g[g.date != date].itertuples()),
                       "niveau": niveau})
    out = pd.DataFrame(lignes)
    out.to_csv(DATA / "retraits_sources.csv", index=False)
    # Reporté dans le référentiel pour les alertes.
    ref = charger()
    o = out.set_index("set")
    ref["retrait_croise"] = ref.base.map(o.retrait_croise)
    ref["niveau_retrait"] = ref.base.map(o.niveau)
    ref["sources_retrait"] = ref.base.map(o.sources)
    # Une date de retrait croisée encore à venir l'emporte sur Brickset seul.
    futur = pd.to_datetime(ref.retrait_croise, errors="coerce") > pd.Timestamp.today().normalize()
    ref.loc[futur & (ref.statut == "retiré"), "statut"] = "en vente"
    ref.to_csv(DATA / "referentiel.csv", index=False)
    return out


if __name__ == "__main__":
    r = construire()
    for d, g in r[r.retrait_croise.str.startswith(str(AN))].groupby("retrait_croise"):
        print(d, len(g), "sets :", g.niveau.value_counts().to_dict(), "| sources :",
              pd.Series(", ".join(g.sources).replace(" (liste Lego)", "").split(", ")).value_counts().to_dict())
