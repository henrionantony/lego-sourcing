"""Historique des prix vus sur Dealabs, par set, pour savoir si un deal est un bon
moment pour acheter (demande du 02/11/2026) : plus bas jamais vu, prix habituel en
promo, nombre de retours du prix, et réduction habituelle par gamme.

Sources : archive des pages du groupe LEGO (data/dealabs_deals.csv, depuis avril 2026),
deals notés dans la base (table deals) et recherche Dealabs par numéro de set (environ
un an, en cache). Le résultat est rangé dans la base (fichiers/historique_prix) pour
que le passage GitHub le lise aussi.

Usage :
  python3 historique_prix.py construire [N]   → recherche N sets de plus (défaut 40) et publie
  python3 historique_prix.py voir <set> [prix]
"""
import datetime as dt
import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LOCAL = DATA / "cache" / "historique_prix.json"
RECH = DATA / "cache" / "historique_recherche.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
RE_BOUTIQUE = re.compile(r"\(([^()]{2,40})\)\s*$")
# Prix réservés à certains clients ou en lot : ne comptent pas comme prix de référence
DOUTEUX = re.compile(r"nouveaux? (clients?|comptes?)|lot de|x ?[2-9]\b|\b[2-9] ?x\b|occasion|reconditionn", re.I)

_H = None


def _boutique(titre: str) -> str:
    m = RE_BOUTIQUE.search(titre or "")
    return m.group(1) if m and not re.search(r"\d|€|via|nouveau", m.group(1), re.I) else ""


def recherche(s: str) -> list[dict]:
    """Deals Dealabs dont le titre contient le numéro du set (recherche du site, ~1 an)."""
    url = "https://www.dealabs.com/search?" + urllib.parse.urlencode({"q": s, "sortBy": "new"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR"})
    h = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    out = []
    for part in h.split('"threadId":')[1:]:
        i = re.match(r'"?(\d+)', part)
        t = re.search(r'"title":"((?:[^"\\]|\\.)+)"', part)
        p = re.search(r'"price":([0-9.]+)', part)
        d = re.search(r'"publishedAt":(\d+)', part)
        if not (i and t and p and d):
            continue
        titre = json.loads('"' + t.group(1) + '"')
        if not re.search(rf"(?<!\d){s}(?!\d)", titre):
            continue
        out.append({"id": i.group(1), "d": dt.datetime.fromtimestamp(int(d.group(1))).date().isoformat(),
                    "p": float(p.group(1)), "t": titre[:120]})
    return list({x["id"]: x for x in out}.values())


def construire(n_recherches: int = 40):
    import pandas as pd
    import base
    par: dict[str, dict[str, dict]] = {}

    def ajouter(s, id_, d, p, t):
        if not s or p is None or p != p or float(p) <= 0:
            return
        par.setdefault(str(s), {})[str(id_)] = {"d": str(d)[:10], "p": round(float(p), 2), "t": str(t)[:120]}

    f = DATA / "dealabs_deals.csv"
    if f.exists():
        for r in pd.read_csv(f, dtype={"set": str, "id": str}).itertuples():
            ajouter(r.set, r.id, r.publie, r.prix, r.titre)
    for r in base.lire("deals", "select=id,titre,prix,vu_le,id_set&limit=10000") or []:
        ajouter(r.get("id_set"), r["id"], r.get("vu_le"), r.get("prix"), r.get("titre"))
    # Recherche par set : d'abord les sets les plus présents (et jamais cherchés)
    cache = json.loads(RECH.read_text()) if RECH.exists() else {}
    vieux = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    a_chercher = sorted((s for s in par if cache.get(s, {}).get("le", "") < vieux), key=lambda s: -len(par[s]))
    for s in a_chercher[:n_recherches]:
        try:
            cache[s] = {"le": dt.date.today().isoformat(), "deals": recherche(s)}
        except Exception as e:
            print("recherche", s, e)
        time.sleep(2)
    RECH.parent.mkdir(parents=True, exist_ok=True)
    RECH.write_text(json.dumps(cache, ensure_ascii=False))
    for s, c in cache.items():
        for x in c.get("deals", []):
            ajouter(s, x["id"], x["d"], x["p"], x["t"])
    hist = {s: sorted(([k, v["d"], v["p"], v["t"]] for k, v in d.items()), key=lambda x: x[1]) for s, d in par.items()}
    base.json_partage_ecrire("historique_prix", LOCAL, hist)
    print(f"{len(hist)} sets, {sum(len(v) for v in hist.values())} deals, {len(a_chercher[:n_recherches])} recherches")


def historique() -> dict:
    global _H
    if _H is None:
        try:
            import base
            _H = base.json_partage("historique_prix", LOCAL, {})
        except Exception:
            _H = json.loads(LOCAL.read_text()) if LOCAL.exists() else {}
    return _H


def _jj(d: str) -> str:
    return f"{d[8:10]}/{d[5:7]}" + ("" if d[:4] == str(dt.date.today().year) else f"/{d[2:4]}")


def _eur(x: float) -> str:
    return f"{x:.2f}".replace(".", ",").removesuffix(",00") + " €"


def verdict(s: str, prix: float, id_deal=None, titre: str = "", jour: str | None = None,
            prix_public: float | None = None, gamme: str | None = None, en: bool = False) -> str | None:
    """Une ligne qui situe le prix dans l'historique du set (deals des jours d'avant)."""
    if not s or not prix:
        return None
    jour = jour or dt.date.today().isoformat()
    lst = [x for x in historique().get(str(s), [])
           if str(x[0]) != str(id_deal) and x[1] < jour and not DOUTEUX.search(x[3] or "")]
    # Un même prix reposté plusieurs jours de suite ne compte qu'une fois par semaine
    vus, prix_l = set(), []
    for _id, d, p, t in lst:
        k = (round(p), d[:8], int(d[8:10]) // 7)
        if k not in vus:
            vus.add(k)
            prix_l.append((p, d, _boutique(t)))
    g = gammes().get(gamme) if gamme else None
    if len(prix_l) < 2:
        if en:
            t = "📊 Little price history for this set" if prix_l else "📊 First deal seen on this set"
        else:
            t = "📊 Peu d'historique sur ce set" if prix_l else "📊 Premier deal vu sur ce set"
        if g and prix_public:
            r = prix / prix_public - 1
            if en:
                t += (f" · {gamme}: usually {g['med']:+.0%}, good from {g['bon']:+.0%}; here {r:+.0%}"
                      + (" (very good)" if r <= g["bon"] else " (fair)" if r <= g["med"] else " (meh)"))
            else:
                t += (f" · {gamme} : d'habitude {g['med']:+.0%}, bon prix dès {g['bon']:+.0%} ; ici {r:+.0%}"
                      + (" (très bon)" if r <= g["bon"] else " (correct)" if r <= g["med"] else " (moyen)"))
        return t
    depuis = _jj(min(d for _, d, _ in prix_l))
    bas = min(prix_l, key=lambda x: x[0])
    med = statistics.median(p for p, _, _ in prix_l)
    proches = sum(1 for p, _, _ in prix_l if p <= prix * 1.05)
    ou = f" ({bas[2]})" if bas[2] else ""
    n = len(prix_l)
    if prix < bas[0] - 0.5:
        return (f"📉 Lowest price ever seen (before: {_eur(bas[0])} on {_jj(bas[1])}{ou}, {n} deals since {depuis})" if en else
                f"📉 Plus bas jamais vu (avant : {_eur(bas[0])} le {_jj(bas[1])}{ou}, {n} deals depuis le {depuis})")
    if prix <= bas[0] * 1.03:
        return (f"📉 Matches the lowest seen ({_eur(bas[0])} on {_jj(bas[1])}{ou}, {n} deals since {depuis})" if en else
                f"📉 Égale le plus bas vu ({_eur(bas[0])} le {_jj(bas[1])}{ou}, {n} deals depuis le {depuis})")
    if prix <= med:
        return (f"✅ Usual good price: seen {proches} times at this level since {depuis}, lowest {_eur(bas[0])} on {_jj(bas[1])}" if en else
                f"✅ Bon prix habituel : vu {proches} fois à ce niveau depuis le {depuis}, plus bas {_eur(bas[0])} le {_jj(bas[1])}")
    return (f"⏳ Often cheaper: usual deal price {_eur(med)}, lowest {_eur(bas[0])} on {_jj(bas[1])}{ou} ({n} deals since {depuis})" if en else
            f"⏳ Souvent moins cher : prix habituel en promo {_eur(med)}, plus bas {_eur(bas[0])} le {_jj(bas[1])}{ou} ({n} deals depuis le {depuis})")


_G = None


def gammes() -> dict:
    """Réduction habituelle par gamme (médiane des deals vs prix LEGO), sur l'historique."""
    global _G
    if _G is not None:
        return _G
    import pandas as pd
    try:
        r = pd.read_csv(DATA / "referentiel.csv", dtype={"base": str}, usecols=["base", "theme_bs", "prix_public"], low_memory=False)
    except Exception:  # passage GitHub : table des prix privée
        from referentiel import charger
        r = charger()[["base", "theme_bs", "prix_public"]]
    r = r.dropna().drop_duplicates("base").set_index("base")
    par: dict[str, list] = {}
    for s, lst in historique().items():
        if s in r.index and r.at[s, "prix_public"] > 0:
            for _id, d, p, t in lst:
                if not DOUTEUX.search(t or ""):
                    par.setdefault(r.at[s, "theme_bs"], []).append(p / r.at[s, "prix_public"] - 1)
    _G = {g: {"n": len(v), "med": round(statistics.median(v), 3),
              "bon": round(sorted(v)[len(v) // 4], 3)} for g, v in par.items() if len(v) >= 8}
    return _G


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "construire":
        construire(int(a[1]) if len(a) > 1 else 40)
    elif a and a[0] == "voir":
        h = historique().get(a[1], [])
        for x in h:
            print(x[1], x[2], x[3])
        if len(a) > 2:
            print(verdict(a[1], float(a[2])))
    elif a and a[0] == "gammes":
        for g, v in sorted(gammes().items(), key=lambda x: x[1]["med"]):
            print(f"{g:30} {v['med']:+.0%} habituel, {v['bon']:+.0%} bon prix ({v['n']} deals)")
