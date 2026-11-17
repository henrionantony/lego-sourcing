"""Prix et offre des sets sur eBay.fr (API officielle Browse, lecture seule).

Pour chaque set suivi (stock + deals récents), lit les annonces neuves à prix fixe
livrables en France : prix demandés (port compris) et nombre d'annonces
concurrentes (l'offre en face de nous).
Résultat dans la base privée, document « ebay/prix » :
  {set: {"n": annonces, "bas": prix le plus bas, "med": médiane, "maj": date}}
Les ventes passées ne sont pas fournies par l'API publique d'eBay (et la quantité
vendue des annonces à un seul exemplaire vaut toujours 0) : pas de signal de demande ici.

Usage : python3 ebay.py [set …]   (sans argument : les sets suivis, les plus anciens d'abord)
Variables : EBAY_CLIENT_ID, EBAY_CLIENT_SECRET (clés « Production » de developer.ebay.com).
"""
import base64
import datetime as dt
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request

import base

API = "https://api.ebay.com"
MARCHE = "EBAY_FR"
PAR_PASSAGE = 1000         # sets relus par passage (quota Browse : 5 000 appels/jour, 1 appel par set)
DEBUG = {} if os.environ.get("EBAY_DEBUG") else None   # détail privé dans la base (ebay/debug)
FRAIS = 3                  # jours avant de relire un set
EXCLUS = re.compile(r"notice|instructions?|bo[iî]te vide|empty box|sans bo[iî]te|minifig|figurine seule|"
                    r"compatible|moc\b|custom|lot de pi[eè]ces|autocollant|sticker|led|[ée]clairage|light kit|lumi[eè]re|"
                    r"d[ée]j[aà] mont[ée]|occasion|used|incomplet|pi[eè]ces d[ée]tach[ée]es", re.I)


def jeton() -> str:
    cle = f"{os.environ['EBAY_CLIENT_ID']}:{os.environ['EBAY_CLIENT_SECRET']}".encode()
    corps = urllib.parse.urlencode({"grant_type": "client_credentials",
                                    "scope": "https://api.ebay.com/oauth/api_scope"}).encode()
    req = urllib.request.Request(API + "/identity/v1/oauth2/token", data=corps, method="POST", headers={
        "Authorization": "Basic " + base64.b64encode(cle).decode(),
        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        # Diagnostic sans rien révéler : type de clé (PRD = production, SBX = bac à sable) et réponse d'eBay
        cid = os.environ["EBAY_CLIENT_ID"]
        sorte = "production" if "-PRD-" in cid else "bac à sable" if "-SBX-" in cid else "inconnue"
        sec = os.environ["EBAY_CLIENT_SECRET"]
        secret = "Cert ID" if sec.startswith("PRD-") else "Dev ID ?" if re.fullmatch(r"[0-9a-f-]{36}", sec) else "autre"
        sys.exit(f"jeton eBay refusé ({e.code}) : clé {sorte}, secret {secret}, longueurs {len(cid)}/{len(os.environ['EBAY_CLIENT_SECRET'])}, "
                 f"réponse {e.read()[:200].decode(errors='replace')}")


def appel(tok: str, chemin: str) -> dict:
    req = urllib.request.Request(API + chemin, headers={
        "Authorization": "Bearer " + tok, "X-EBAY-C-MARKETPLACE-ID": MARCHE,
        "Accept-Language": "fr-FR"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def prix(a: dict) -> float | None:
    try:
        p = float(a["price"]["value"])
    except (KeyError, ValueError):
        return None
    port = (a.get("shippingOptions") or [{}])[0].get("shippingCost", {}).get("value")
    return round(p + float(port or 0), 2)


def lire_set(tok: str, s: str) -> dict:
    q = urllib.parse.urlencode({
        "q": f"lego {s}", "limit": 100,
        "filter": "conditionIds:{1000},buyingOptions:{FIXED_PRICE},deliveryCountry:FR,priceCurrency:EUR"})
    r = appel(tok, "/buy/browse/v1/item_summary/search?" + q)
    motif = re.compile(rf"(?<!\d){re.escape(s)}(?!\d)")
    bons = [a for a in r.get("itemSummaries", [])
            if motif.search(a.get("title", "")) and not EXCLUS.search(a.get("title", "")) and prix(a)]
    # Écarte les annonces trop bon marché pour être le set complet (pièces, figurines, notices oubliées par le filtre)
    if bons:
        m = statistics.median(prix(a) for a in bons)
        bons = [a for a in bons if prix(a) >= 0.5 * m]
    ps = sorted(prix(a) for a in bons)
    out = {"n": len(bons), "maj": dt.date.today().isoformat()}
    if ps:
        out["bas"] = ps[0]
        out["med"] = round(statistics.median(ps), 2)
    if DEBUG is not None:
        DEBUG[s] = [[a.get("title", "")[:80], prix(a)] for a in sorted(bons, key=prix)[:5]]
    return out


def sets_suivis() -> list[str]:
    s = set(base.stock_par_set())
    depuis = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    for d in base.lire("deals", f"select=id_set&vu_le=gte.{depuis}&id_set=not.is.null&limit=10000") or []:
        s.add(str(d["id_set"]).split("-")[0].removesuffix(".0"))
    return sorted(x for x in s if x.isdigit())


def main():
    tok = jeton()
    deja = base.doc("ebay/prix") or {}
    if len(sys.argv) > 1:
        a_lire = sys.argv[1:]
    else:
        limite = (dt.date.today() - dt.timedelta(days=FRAIS)).isoformat()
        a_lire = sorted((s for s in sets_suivis() if deja.get(s, {}).get("maj", "") <= limite),
                        key=lambda s: deja.get(s, {}).get("maj", ""))[:PAR_PASSAGE]
    ok = err = 0
    for s in a_lire:
        try:
            deja[s] = lire_set(tok, s)
            ok += 1
        except Exception as e:
            err += 1
            if err == 1:
                print("première erreur :", type(e).__name__, str(e)[:200])
    if DEBUG:
        base.doc_ecrire("ebay/debug", DEBUG)
    if ok and not base.doc_ecrire("ebay/prix", deja):
        sys.exit("écriture dans la base refusée")
    # Journal public : seulement des compteurs
    avec = sum(1 for v in deja.values() if v.get("n"))
    print(f"{ok} sets lus, {err} erreurs, {len(deja)} sets connus dont {avec} avec annonces")
    if err and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
