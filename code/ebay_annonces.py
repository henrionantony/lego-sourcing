"""Publie sur eBay.fr les annonces préparées par ebay_preparer.py (document « ebay/a_publier »).

API officielles eBay (Sell Inventory / Account), avec l'autorisation du vendeur
(EBAY_REFRESH_TOKEN) ; lieu d'expédition EBAY_LIEU (« Ville CodePostal »). Une fois pour toutes :
politiques de paiement, de retour (non acceptés) et de port (une par grille France / UE / RU),
lieu d'expédition. Puis par set : fiche produit, offre, publication. Résultat dans « ebay/annonces »
{set: {"sku", "offre", "listing", "prix", "qte", "le"}} ; aucune donnée d'acheteur n'est gardée.
Journal public : seulement des compteurs ; le détail des erreurs va dans « journal/ebay ».

Usage : python3 ebay_annonces.py [--essai]   (--essai : tout sauf la publication)
"""
import base64
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import base

API = "https://api.ebay.com"
MARCHE = "EBAY_FR"
SCOPES = " ".join("https://api.ebay.com/oauth/api_scope/" + s for s in ("sell.inventory", "sell.account"))
LIEU = "legohq"
ISO3 = {"AUT": "AT", "BEL": "BE", "BGR": "BG", "HRV": "HR", "CYP": "CY", "CZE": "CZ", "DNK": "DK", "EST": "EE",
        "FIN": "FI", "DEU": "DE", "GRC": "GR", "HUN": "HU", "IRL": "IE", "ITA": "IT", "LVA": "LV", "LTU": "LT",
        "LUX": "LU", "MLT": "MT", "NLD": "NL", "POL": "PL", "PRT": "PT", "ROU": "RO", "SVK": "SK", "SVN": "SI",
        "ESP": "ES", "SWE": "SE", "CHE": "CH", "GBR": "GB", "FRA": "FR"}
UE = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
      "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "CH"]
JOURNAL = []
PAR_JOUR = int(os.environ.get("EBAY_PAR_JOUR") or 5)
MAX_EN_LIGNE = 150   # annonces gratuites par mois (une annonce renouvelée chaque mois compte à nouveau)


class Erreur(Exception):
    pass


def jeton() -> str:
    cle = f"{os.environ['EBAY_CLIENT_ID']}:{os.environ['EBAY_CLIENT_SECRET']}".encode()
    corps = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": os.environ["EBAY_REFRESH_TOKEN"].strip(),
                                    "scope": SCOPES}).encode()
    req = urllib.request.Request(API + "/identity/v1/oauth2/token", data=corps, method="POST", headers={
        "Authorization": "Basic " + base64.b64encode(cle).decode(), "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        rt = os.environ["EBAY_REFRESH_TOKEN"].strip()
        if len(rt) > 1000:
            # Jeton d'accès (User Token, valable 2 h) collé à la place du jeton de renouvellement : on s'en sert tel quel
            print("jeton d'accès utilisé directement (valable 2 h)")
            return rt
        sorte = "inconnue"
        sys.exit(f"autorisation vendeur refusée ({e.code}) : jeton {sorte}, longueur {len(rt)}, "
                 f"réponse {e.read()[:200].decode(errors='replace')}")


TOK = None


def appel(methode: str, chemin: str, corps=None, ok_404=False, _relance=False):
    data = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(API + chemin, data=data, method=methode, headers={
        "Authorization": "Bearer " + TOK, "Content-Type": "application/json", "Accept": "application/json",
        "Content-Language": "fr-FR", "Accept-Language": "fr-FR", "X-EBAY-C-MARKETPLACE-ID": MARCHE})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read()
            return json.loads(t) if t else {}
    except urllib.error.HTTPError as e:
        t = e.read().decode(errors="replace")
        if ok_404 and e.code == 404:
            return None
        if e.code >= 500 and not _relance:
            import time
            time.sleep(3)
            return appel(methode, chemin, corps, ok_404, True)
        raise Erreur(f"{methode} {chemin.split('?')[0]} → {e.code} {t[:600]}")


def creer(chemin: str, corps: dict, champ: str) -> str:
    """Crée une politique ; si eBay en a déjà une identique (« Conditions en double »), reprend celle-là."""
    try:
        return appel("POST", chemin, corps)[champ]
    except Erreur as e:
        m = re.search(r'"duplicatePolicyId","value":"(\d+)"', str(e))
        if m:
            return m.group(1)
        raise


def politiques() -> dict:
    """Paiement et retour (créés une fois), plus la fonction qui donne la politique de port d'une grille."""
    try:
        appel("POST", "/sell/account/v1/program/opt_in", {"programType": "SELLING_POLICY_MANAGEMENT"})
    except Erreur as e:
        if "already" not in str(e).lower() and "20403" not in str(e) and "409" not in str(e):
            JOURNAL.append(f"opt_in : {e}")
    q = f"?marketplace_id={MARCHE}"
    pay = (appel("GET", "/sell/account/v1/payment_policy" + q) or {}).get("paymentPolicies", [])
    pay = next((p["paymentPolicyId"] for p in pay if p["name"] == "LegoHQ paiement"), None) or creer(
        "/sell/account/v1/payment_policy", {"name": "LegoHQ paiement", "marketplaceId": MARCHE,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}], "immediatePay": True}, "paymentPolicyId")
    ret = (appel("GET", "/sell/account/v1/return_policy" + q) or {}).get("returnPolicies", [])
    ret = next((p["returnPolicyId"] for p in ret if p["name"] == "LegoHQ sans retour"), None) or creer(
        "/sell/account/v1/return_policy", {"name": "LegoHQ sans retour", "marketplaceId": MARCHE,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}], "returnsAccepted": False}, "returnPolicyId")
    ful = {p["name"]: p["fulfillmentPolicyId"] for p in
           (appel("GET", "/sell/account/v1/fulfillment_policy" + q) or {}).get("fulfillmentPolicies", [])}
    services = services_port()

    pays_ue = list(UE)   # réduit aux destinations qu'eBay.fr accepte une par une (sinon refus 216347)

    def port(p: dict) -> str:
        nom = f"LegoHQ port FR {p['fr']:.2f} UE {p['ue']:.2f} RU {p['uk']:.2f}"
        if nom in ful:
            return ful[nom]
        eur = lambda x: {"value": f"{x:.2f}", "currency": "EUR"}
        for _ in range(3):
            corps = {
                "name": nom, "marketplaceId": MARCHE, "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
                "handlingTime": {"value": 2, "unit": "DAY"},
                "shippingOptions": [
                    {"optionType": "DOMESTIC", "costType": "FLAT_RATE", "shippingServices": [
                        {"shippingServiceCode": services["fr"], "shippingCost": eur(p["fr"]), "sortOrder": 1}]},
                    {"optionType": "INTERNATIONAL", "costType": "FLAT_RATE", "shippingServices": [
                        {"shippingServiceCode": services["int"], "shippingCost": eur(p["ue"]), "sortOrder": 1,
                         "shipToLocations": {"regionIncluded": [{"regionName": c} for c in pays_ue]}},
                        {"shippingServiceCode": services["int"], "shippingCost": eur(p["uk"]), "sortOrder": 2,
                         "shipToLocations": {"regionIncluded": [{"regionName": "GB"}]}}]}],
                "shipToLocations": {"regionIncluded": [{"regionName": c} for c in pays_ue + ["GB", "FR"]]}}
            try:
                ful[nom] = creer("/sell/account/v1/fulfillment_policy", corps, "fulfillmentPolicyId")
                return ful[nom]
            except Erreur as e:
                m = re.search(r'"name":"([A-Z,]{3,})","value":"[^"]*destinations', str(e))
                if not m:
                    raise
                refus = {ISO3.get(x, x) for x in m.group(1).split(",")}
                pays_ue[:] = [c for c in pays_ue if c not in refus]
                JOURNAL.append(f"destinations refusées par eBay.fr : {' '.join(sorted(refus))}")
        raise Erreur("politique de port impossible")
    return {"paiement": pay, "retour": ret, "port": port}


def services_port() -> dict:
    """Codes eBay des services Colissimo (France et international), lus dans les détails du site eBay.fr
    (API Trading GeteBayDetails, site 71)."""
    import re
    corps = ('<?xml version="1.0" encoding="utf-8"?><GeteBayDetailsRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
             '<DetailName>ShippingServiceDetails</DetailName></GeteBayDetailsRequest>').encode()
    req = urllib.request.Request(API + "/ws/api.dll", data=corps, method="POST", headers={
        "X-EBAY-API-SITEID": "71", "X-EBAY-API-COMPATIBILITY-LEVEL": "1225", "X-EBAY-API-CALL-NAME": "GeteBayDetails",
        "X-EBAY-API-IAF-TOKEN": TOK, "Content-Type": "text/xml"})
    liste = []
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            x = r.read().decode(errors="replace")
        for b in re.findall(r"<ShippingServiceDetails>(.*?)</ShippingServiceDetails>", x, re.S):
            g = lambda t: (re.search(rf"<{t}>(.*?)</{t}>", b, re.S) or [None, ""])[1]
            if g("ValidForSellingFlow") == "true":
                liste.append((g("ShippingService"), g("Description"), g("InternationalService") == "true"))
    except Exception as e:
        JOURNAL.append(f"services de port : {e}")
    choisir = lambda inter: next((c for c, d, i in liste if i == inter and "colissimo" in (c + d).lower()), None)
    fr, it = choisir(False), choisir(True)
    JOURNAL.append(f"services : {fr} / {it} parmi {len(liste)} ; colissimo : "
                   + ", ".join(f"{c}{'*' if i else ''}" for c, d, i in liste if "coli" in (c + d).lower()))
    if not fr or not it:
        raise Erreur("services Colissimo introuvables")
    return {"fr": fr, "int": it}


def lieu():
    if appel("GET", f"/sell/inventory/v1/location/{LIEU}", ok_404=True):
        return
    ville, cp = os.environ["EBAY_LIEU"].strip().rsplit(" ", 1)
    appel("POST", f"/sell/inventory/v1/location/{LIEU}", {
        "location": {"address": {"city": ville, "postalCode": cp, "country": "FR"}},
        "locationTypes": ["WAREHOUSE"], "name": "LegoHQ", "merchantLocationStatus": "ENABLED"})


_ARBRE = None


def categorie(v: dict) -> tuple[str, dict]:
    """Catégorie suggérée par eBay et caractéristiques remplies (obligatoires d'abord)."""
    global _ARBRE
    if _ARBRE is None:
        _ARBRE = appel("GET", f"/commerce/taxonomy/v1/get_default_category_tree_id?marketplace_id={MARCHE}")["categoryTreeId"]
    sug = appel("GET", f"/commerce/taxonomy/v1/category_tree/{_ARBRE}/get_category_suggestions?q="
                + urllib.parse.quote(f"LEGO {v['nom']} set complet"))
    cats = [c["category"]["categoryId"] for c in sug.get("categorySuggestions", [])]
    noms = [c["category"]["categoryName"] for c in sug.get("categorySuggestions", [])]
    # Préférer « ensembles complets » LEGO
    i = next((k for k, n in enumerate(noms) if "complet" in n.lower() or "complete" in n.lower()), 0)
    cat = cats[i]
    asp = appel("GET", f"/commerce/taxonomy/v1/category_tree/{_ARBRE}/get_item_aspects_for_category?category_id={cat}")
    connu = {"marque": "LEGO", "numéro": v["set"], "mpn": v["set"], "référence": v["set"], "gamme": v.get("gamme"),
             "thème": v.get("gamme"), "nombre de pièces": v.get("pieces"), "année": v.get("annee"), "ean": v.get("ean"),
             "type": "Ensemble complet", "caractéristiques": "Boîte scellée", "nom": v["nom"]}
    out = {}
    for a in asp.get("aspects", []):
        n = a["localizedAspectName"]
        val = next((str(x) for k, x in connu.items() if x and k in n.lower()), None)
        valeurs = [x["localizedValue"] for x in a.get("aspectValues", [])]
        if val and a.get("aspectConstraint", {}).get("aspectMode") == "SELECTION_ONLY":
            val = next((x for x in valeurs if x.lower() == val.lower()), None)
        if val:
            out[n] = [val]
        elif a.get("aspectConstraint", {}).get("aspectRequired"):
            # Obligatoire sans valeur connue : première valeur proposée qui ne dit rien de faux
            sur = next((x for x in valeurs if x.lower() in ("lego", "non applicable", "ne s'applique pas", "sans objet")), None)
            if sur:
                out[n] = [sur]
            else:
                raise Erreur(f"caractéristique obligatoire « {n} » inconnue (catégorie {noms[i]})")
    return cat, out


def publier(s: str, v: dict, pol: dict, essai: bool, deja: dict | None = None) -> dict:
    v = {**v, "set": s}
    sku = f"LHQ-{s}"
    cat, aspects = categorie(v)
    produit = {"title": v["titre"], "description": v["description"], "aspects": aspects, "imageUrls": v["images"],
               "brand": "LEGO", "mpn": s}
    if v.get("ean"):
        produit["ean"] = [v["ean"]]
    appel("PUT", f"/sell/inventory/v1/inventory_item/{sku}", {
        "product": produit, "condition": "NEW",
        "availability": {"shipToLocationAvailability": {"quantity": int(v["qte"])}},
        "packageWeightAndSize": {"weight": {"value": v["port"]["kg"], "unit": "KILOGRAM"}}})
    offre = {"sku": sku, "marketplaceId": MARCHE, "format": "FIXED_PRICE", "availableQuantity": int(v["qte"]),
             "categoryId": cat, "listingDescription": v["description"], "merchantLocationKey": LIEU,
             "pricingSummary": {"price": {"value": f"{v['prix']:.2f}", "currency": "EUR"}},
             "listingPolicies": {"fulfillmentPolicyId": pol["port"](v["port"]), "paymentPolicyId": pol["paiement"],
                                 "returnPolicyId": pol["retour"]}}
    try:
        ex = appel("GET", f"/sell/inventory/v1/offer?sku={sku}&marketplace_id={MARCHE}", ok_404=True) or {}
    except Erreur:
        ex = {}
    ex = (ex.get("offers") or [None])[0]
    if ex:
        oid = ex["offerId"]
        appel("PUT", f"/sell/inventory/v1/offer/{oid}", offre)
    else:
        oid = appel("POST", "/sell/inventory/v1/offer", offre)["offerId"]
    res = {"sku": sku, "offre": oid, "prix": v["prix"], "qte": v["qte"], "port": v["port"],
           "le": dt.datetime.now().isoformat(timespec="minutes")}
    if deja and deja.get("listing"):
        res["listing"] = deja["listing"]          # annonce en ligne : la mise à jour de l'offre suffit
    elif not essai:
        res["listing"] = appel("POST", f"/sell/inventory/v1/offer/{oid}/publish")["listingId"]
    return res


def main():
    global TOK
    essai = "--essai" in sys.argv
    TOK = jeton()
    a_faire = base.doc("ebay/a_publier") or {}
    faites = base.doc("ebay/annonces") or {}
    # Annonces en ligne dont le prix ou le port a changé : mises à jour ; nouvelles : PAR_JOUR au plus
    # (rythme choisi, et 150 annonces gratuites par mois sur eBay.fr)
    maj = {s: v for s, v in a_faire.items() if (faites.get(s) or {}).get("listing")
           and (faites[s].get("prix") != v["prix"] or faites[s].get("port") != v["port"])}
    en_ligne = sum(1 for v in faites.values() if v.get("listing"))
    neuves = [s for s in a_faire if not (faites.get(s) or {}).get("listing")][:max(0, min(PAR_JOUR, MAX_EN_LIGNE - en_ligne))]
    a_faire = {s: a_faire[s] for s in list(maj) + neuves}
    if not a_faire:
        print("rien à publier")
        return
    try:
        pol = politiques()
        lieu()
    except Erreur as e:
        JOURNAL.append(f"préparation : {e}")
        base.doc_ecrire("journal/ebay", {"le": dt.datetime.now().isoformat(timespec="minutes"), "lignes": JOURNAL})
        sys.exit("préparation du compte eBay impossible (détail dans la base)")
    ok = err = 0
    for s, v in a_faire.items():
        try:
            faites[s] = publier(s, v, pol, essai, faites.get(s))
            ok += 1
        except Erreur as e:
            err += 1
            JOURNAL.append(f"{s} : {e}")
    base.doc_ecrire("ebay/annonces", faites)
    base.doc_ecrire("journal/ebay", {"le": dt.datetime.now().isoformat(timespec="minutes"), "lignes": JOURNAL})
    print(f"{ok} annonces traitées ({len(maj)} mises à jour, {len(neuves)} nouvelles{', essai' if essai else ''}), {err} erreurs")
    if err and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
