"""Sonde : quels sites de magasins répondent à un robot (depuis GitHub) ?

Pour chaque site, on charge une recherche « lego » comme le ferait un navigateur
et on note le code HTTP, la taille et la présence d'un mur anti-robot. Lecture
seule, aucun achat, aucun compte. Usage : python3 sondes.py
"""
import re
import time
import urllib.error
import urllib.request

SITES = {
    "Carrefour": "https://www.carrefour.fr/s?q=lego",
    "Leclerc": "https://www.e.leclerc/recherche?q=lego",
    "Auchan": "https://www.auchan.fr/recherche?text=lego",
    "Amazon": "https://www.amazon.fr/s?k=lego",
    "Fnac": "https://www.fnac.com/SearchResult/ResultList.aspx?Search=lego",
    "Cdiscount": "https://www.cdiscount.com/search/10/lego.html",
    "Cultura": "https://www.cultura.com/search/results?search_query=lego",
    "King Jouet": "https://www.king-jouet.com/jeux-jouets/recherche.htm?q=lego",
    "JouéClub": "https://www.joueclub.fr/recherche?q=lego",
    "Lego.com": "https://www.lego.com/fr-fr/categories/sales-and-deals",
    "Intermarché": "https://www.intermarche.com/recherche/lego",
    "Smyths": "https://www.smythstoys.com/fr/fr-fr/search/?text=lego",
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")
MURS = r"datadome|captcha|cf-chl|challenge-platform|access denied|robot|px-captcha|perimeterx|akamai"


def sonder(nom: str, url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9",
                                               "Accept": "text/html,application/xhtml+xml"})
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=20)
        h, code = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        h, code = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:
        return f"{nom:12} ERREUR {type(e).__name__}: {e}"
    mur = re.search(MURS, h, re.I)
    prix = len(re.findall(r"\d+[,.]\d{2}\s*(?:€|&euro;|\\u20ac)", h))
    lego = len(re.findall(r"\b\d{5}\b", h))
    return (f"{nom:12} HTTP {code} · {len(h) // 1024} Ko · {time.time() - t0:.1f}s · "
            f"{prix} prix · {lego} numéros · mur : {mur.group(0) if mur else 'non'}")


def detail(nom: str):
    """Où sont les prix dans la page (JSON embarqué) : quelques extraits autour de « price »."""
    req = urllib.request.Request(SITES[nom], headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9",
                                                      "Accept": "text/html,application/xhtml+xml"})
    try:
        h = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        h = e.read().decode("utf-8", "replace")
    cles = re.findall(r'"([A-Za-z_]*[Pp]rice[A-Za-z_]*)"\s*:', h)
    print(f"--- {nom} : clés prix {sorted(set(cles))[:15]} ({len(cles)})")
    for m in list(re.finditer(r'"price"|data-price|itemprop="price"|product-price', h))[:3]:
        print("   …", re.sub(r"\s+", " ", h[max(0, m.start() - 250):m.end() + 80]))
    print("   api :", sorted(set(re.findall(r'["\'](/?(?:https://[^"\']+)?/api/[^"\'\s]{3,80})', h)))[:12])
    m = re.search(r"\b(4\d{4}|6\d{4}|7\d{4}|1\d{4}|2\d{4}|3\d{4})\b", h)
    if m:
        print("   numéro :", re.sub(r"\s+", " ", h[max(0, m.start() - 200):m.end() + 200]))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        for nom in sys.argv[1:]:
            detail(nom)
        sys.exit()
    for nom, url in SITES.items():
        print(sonder(nom, url), flush=True)
        time.sleep(2)
