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
import html
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import base

API = "https://api.ebay.com"
MARCHE = "EBAY_FR"
SCOPES = " ".join(["https://api.ebay.com/oauth/api_scope"]  # catégories (Taxonomy)
                  + ["https://api.ebay.com/oauth/api_scope/" + s for s in ("sell.inventory", "sell.account")])
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
    # Jeton de renouvellement obtenu avec ebay_jeton.py (rangé chiffré), sinon le secret GitHub
    import ebay_jeton
    rt = ebay_jeton.jeton_range() or os.environ.get("EBAY_REFRESH_TOKEN", "").strip()
    print(f"::add-mask::{rt}")
    corps = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rt, "scope": SCOPES}).encode()
    req = urllib.request.Request(API + "/identity/v1/oauth2/token", data=corps, method="POST", headers={
        "Authorization": "Basic " + base64.b64encode(cle).decode(), "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
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
    remise = remise_groupee()

    pays_ue = list(UE)   # réduit aux destinations qu'eBay.fr accepte une par une (sinon refus 216347)

    def port(p: dict) -> str:
        # Figurine : lettre suivie, France seulement. « add » = port de chaque exemplaire en plus (même annonce)
        lettre, add = bool(p.get("lettre")), p.get("add")
        nom = (f"LegoHQ lettre FR {p['fr']:.2f}" if lettre else f"LegoHQ port FR {p['fr']:.2f} UE {p['ue']:.2f} RU {p['uk']:.2f}") \
            + (f" +{add:.2f}" if add else "") + (" groupé" if remise else "")
        if nom in ful:
            return ful[nom]
        eur = lambda x: {"value": f"{x:.2f}", "currency": "EUR"}
        plus = lambda x: {"additionalShippingCost": eur(add or x)}
        for _ in range(3):
            fr = {"shippingServiceCode": services["lettre"] if lettre else services["fr"], "shippingCost": eur(p["fr"]),
                  "sortOrder": 1, **plus(p["fr"])}
            options = [{"optionType": "DOMESTIC", "costType": "FLAT_RATE", "shippingServices": [fr]}]
            if remise:
                options[0]["shippingDiscountProfileId"] = remise
            if not lettre:
                options.append({"optionType": "INTERNATIONAL", "costType": "FLAT_RATE", "shippingServices": [
                    {"shippingServiceCode": services["int"], "shippingCost": eur(p["ue"]), "sortOrder": 1, **plus(p["ue"]),
                     "shipToLocations": {"regionIncluded": [{"regionName": c} for c in pays_ue]}},
                    {"shippingServiceCode": services["int"], "shippingCost": eur(p["uk"]), "sortOrder": 2, **plus(p["uk"]),
                     "shipToLocations": {"regionIncluded": [{"regionName": "GB"}]}}]})
            corps = {
                "name": nom, "marketplaceId": MARCHE, "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
                "handlingTime": {"value": 2, "unit": "DAY"}, "shippingOptions": options,
                "shipToLocations": {"regionIncluded": [{"regionName": c} for c in (["FR"] if lettre else pays_ue + ["GB", "FR"])]}}
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
    # Figurines : lettre suivie (« lettre » et « suivi » dans le nom), sinon Colissimo
    lettres = [(c, d) for c, d, i in liste if not i and "lettre" in (c + d).lower()]
    lettre = next((c for c, d in lettres if "suivi" in (c + d).lower()), None) or (lettres[0][0] if lettres else fr)
    JOURNAL.append(f"services : {fr} / {it} / lettre {lettre} parmi {len(liste)} ; colissimo et lettres : "
                   + ", ".join(f"{c}{'*' if i else ''}" for c, d, i in liste if "coli" in (c + d).lower() or "lettre" in (c + d).lower()))
    if not fr or not it:
        raise Erreur("services Colissimo introuvables")
    return {"fr": fr, "int": it, "lettre": lettre}


REMISE = 3.0   # envoi groupé : chaque article en plus coûte son port moins 3 € (figurine 4 € → +1 €)


def trading(appel_nom: str, corps: str) -> str:
    req = urllib.request.Request(API + "/ws/api.dll", data=(
        f'<?xml version="1.0" encoding="utf-8"?><{appel_nom}Request xmlns="urn:ebay:apis:eBLBaseComponents">'
        f'{corps}</{appel_nom}Request>').encode(), method="POST", headers={
        "X-EBAY-API-SITEID": "71", "X-EBAY-API-COMPATIBILITY-LEVEL": "1225", "X-EBAY-API-CALL-NAME": appel_nom,
        "X-EBAY-API-IAF-TOKEN": TOK, "Content-Type": "text/xml"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode(errors="replace")


def remise_groupee() -> str | None:
    """Règle d'envoi groupé du compte (API Trading) : « déduire REMISE € du port de chaque article en plus ».
    Créée une fois ; sans elle, chaque article garde son port plein."""
    nom = f"LegoHQ groupe -{REMISE:.0f}"
    try:
        x = trading("GetShippingDiscountProfiles", "")
        for b in re.findall(r"<DiscountProfile>(.*?)</DiscountProfile>", x, re.S):
            if f"<DiscountProfileName>{nom}</DiscountProfileName>" in b:
                return re.search(r"<DiscountProfileID>(.*?)</DiscountProfileID>", b).group(1)
        x = trading("SetShippingDiscountProfiles",
                    "<CurrencyID>EUR</CurrencyID><CombinedDuration>Days_14</CombinedDuration><ModifyActionCode>Add</ModifyActionCode><FlatShippingDiscount>"
                    "<DiscountName>EachAdditionalAmountOff</DiscountName><DiscountProfile>"
                    f"<DiscountProfileName>{nom}</DiscountProfileName>"
                    f'<EachAdditionalAmountOff currencyID="EUR">{REMISE:.2f}</EachAdditionalAmountOff>'
                    "</DiscountProfile></FlatShippingDiscount>")
        if "<Ack>Failure</Ack>" in x:
            JOURNAL.append("remise groupée refusée : " + " | ".join(re.findall(r"<LongMessage>(.*?)</LongMessage>", x))[:400])
            return None
        x = trading("GetShippingDiscountProfiles", "")
        for b in re.findall(r"<DiscountProfile>(.*?)</DiscountProfile>", x, re.S):
            if f"<DiscountProfileName>{nom}</DiscountProfileName>" in b:
                JOURNAL.append("remise groupée créée")
                return re.search(r"<DiscountProfileID>(.*?)</DiscountProfileID>", b).group(1)
        JOURNAL.append("remise groupée introuvable après création")
    except Exception as e:
        JOURNAL.append(f"remise groupée : {e}")
    return None


def manuelles(faites: dict) -> dict:
    """Annonces en ligne sur le compte eBay qui ne viennent pas de l'outil (mises à la main) :
    {numéro de set: [annonces]}. Leurs sets ne reçoivent pas de nouvelle annonce (pas de doublon).
    Liste gardée dans ebay/manuelles (titre, prix, quantité : jamais de données d'acheteur)."""
    nos = {str(v.get("listing")) for v in faites.values() if v.get("listing")}
    # Annonces déjà terminées par l'outil : GetMyeBaySelling les renvoie encore un temps comme actives
    t = base.doc("ebay/manuelles_terminees") or {}
    nos |= {str(x.get("id")) for x in (t.get("terminees") or []) + [e for e in t.get("erreurs") or []
                                                                     if "terminée" in (e.get("erreur") or "")] if isinstance(x, dict)}
    out, page = [], 1
    try:
        while page <= 20:
            x = trading("GetMyeBaySelling", "<ActiveList><Include>true</Include><Pagination>"
                        f"<EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination></ActiveList>"
                        "<DetailLevel>ReturnAll</DetailLevel>")
            if "<Ack>Failure</Ack>" in x:
                JOURNAL.append("annonces du compte illisibles : " + " | ".join(re.findall(r"<LongMessage>(.*?)</LongMessage>", x))[:300])
                return {}
            act = x.split("<ActiveList>", 1)[-1]
            for b in re.findall(r"<Item>(.*?)</Item>", act, re.S):
                i = re.search(r"<ItemID>(\d+)</ItemID>", b)
                if not i or i.group(1) in nos:
                    continue
                t = html.unescape((re.search(r"<Title>(.*?)</Title>", b, re.S) or [None, ""])[1])
                pr = re.search(r"<CurrentPrice[^>]*>([\d.]+)</CurrentPrice>", b)
                q = re.search(r"<QuantityAvailable>(\d+)</QuantityAvailable>", b)
                out.append({"id": i.group(1), "titre": t, "prix": float(pr.group(1)) if pr else None,
                            "qte": int(q.group(1)) if q else None, "sets": re.findall(r"\b(\d{4,7})\b", t)})
            pages = re.search(r"<TotalNumberOfPages>(\d+)</TotalNumberOfPages>", act)
            if not pages or page >= int(pages.group(1)):
                break
            page += 1
    except Exception as e:
        JOURNAL.append(f"annonces du compte : {e}")
        return {}
    base.doc_ecrire("ebay/manuelles", {"le": dt.datetime.now().isoformat(timespec="minutes"), "annonces": out})
    par: dict[str, list] = {}
    for a in out:
        for n in a["sets"]:
            par.setdefault(n, []).append(a)
    return par


def terminer_manuelles(faites: dict, doublons: bool = False):
    """Termine sur eBay toutes les annonces faites à la main (pas par l'outil), à ta demande :
    on repart de zéro avec les prix calculés. Titres gardés dans ebay/manuelles_terminees."""
    manuelles(faites)
    liste = (base.doc("ebay/manuelles") or {}).get("annonces", [])
    if doublons:
        # Seulement les sets que l'outil remet en vente, ou que tu n'as plus (« absents » des réglages)
        garde = {k.split("-")[0] for k in base.doc("ebay/a_publier") or {}} | set((base.doc("ebay/reglages") or {}).get("absents", []))
        liste = [a for a in liste if set(a["sets"]) & garde]
    fini, ko = [], []
    for a in liste:
        try:
            x = trading("EndItem", f"<ItemID>{a['id']}</ItemID><EndingReason>NotAvailable</EndingReason>")
            (ko if "<Ack>Failure</Ack>" in x else fini).append(a if "<Ack>Failure</Ack>" not in x else
                {**a, "erreur": " | ".join(re.findall(r"<LongMessage>(.*?)</LongMessage>", x))[:200]})
        except Exception as e:
            ko.append({**a, "erreur": str(e)[:200]})
    base.doc_ecrire("ebay/manuelles_terminees", {"le": dt.datetime.now().isoformat(timespec="minutes"), "terminees": fini, "erreurs": ko})
    print(f"{len(fini)} annonces faites à la main terminées, {len(ko)} erreurs")



def empreinte(v: dict) -> str:
    """Empreinte du titre et de la description : une annonce en ligne est mise à jour quand ils changent."""
    import hashlib
    return hashlib.sha1((v.get("titre", "") + v.get("description", "")).encode()).hexdigest()[:12]

_IMG = {}


def images_ok(urls: list) -> list:
    """Garde les photos qu'eBay pourra télécharger (réponse 200 et vraie image), 24 au plus."""
    out = []
    for u in urls:
        if u not in _IMG:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    _IMG[u] = r.status == 200 and r.headers.get("Content-Type", "").startswith("image") and len(r.read()) > 2000
            except Exception:
                _IMG[u] = False
        if _IMG[u] and u not in out:
            out.append(u)
    return out[:24]


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
    fig = bool(v.get("figurine"))
    sug = appel("GET", f"/commerce/taxonomy/v1/category_tree/{_ARBRE}/get_category_suggestions?q="
                + urllib.parse.quote(f"LEGO minifigure {v['nom']}" if fig else f"LEGO {v['nom']} set complet"))
    cats = [c["category"]["categoryId"] for c in sug.get("categorySuggestions", [])]
    noms = [c["category"]["categoryName"] for c in sug.get("categorySuggestions", [])]
    # Préférer « ensembles complets » LEGO, ou la catégorie des minifigs
    mots = ("minifig", "figurine") if fig else ("complet", "complete")
    i = next((k for k, n in enumerate(noms) if any(m in n.lower() for m in mots)), 0)
    cat = cats[i]
    asp = appel("GET", f"/commerce/taxonomy/v1/category_tree/{_ARBRE}/get_item_aspects_for_category?category_id={cat}")
    connu = {"marque": "LEGO", "numéro": v["set"], "mpn": v["set"], "référence": v["set"], "gamme": v.get("gamme"),
             "thème": v.get("gamme"), "nombre de pièces": v.get("pieces"), "année": v.get("annee"), "ean": v.get("ean"),
             "type": "Ensemble complet", "caractéristiques": "Boîte scellée", "nom": v["nom"]}
    if fig:
        connu.update({"numéro": v["code"], "mpn": v["code"], "référence": v["code"], "type": "Minifigurine",
                      "caractéristiques": None, "personnage": v["nom"], "nombre de pièces": None})
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
    images = images_ok(v["images"])
    if not images:
        raise Erreur("aucune photo téléchargeable")
    produit = {"title": v["titre"], "description": v["description"], "aspects": aspects, "imageUrls": images,
               "brand": "LEGO", "mpn": v.get("code") or s}
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
    # « Faire une offre » : accepte seul à ~7 % sous le prix, refuse seul sous 80 % du prix
    # ou sous le plancher de marge ; entre les deux, l'offre attend ta réponse
    if v.get("plancher") and v["plancher"] + 1 < v["prix"]:
        refus = float(max(v["plancher"], round(v["prix"] * 0.80)))
        accepte = max(refus + 1, round(v["prix"] * 0.93))
        if accepte < v["prix"]:
            offre["listingPolicies"]["bestOfferTerms"] = {
                "bestOfferEnabled": True,
                "autoAcceptPrice": {"value": f"{accepte:.2f}", "currency": "EUR"},
                "autoDeclinePrice": {"value": f"{refus:.2f}", "currency": "EUR"}}
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
    res = {"sku": sku, "offre": oid, "prix": v["prix"], "qte": v["qte"], "port": v["port"], "img": len(v["images"]), "desc": empreinte(v), "plancher": v.get("plancher"),
           "le": dt.datetime.now().isoformat(timespec="minutes")}
    if deja and deja.get("listing"):
        res["listing"] = deja["listing"]          # annonce en ligne : la mise à jour de l'offre suffit
    elif not essai:
        try:
            res["listing"] = appel("POST", f"/sell/inventory/v1/offer/{oid}/publish")["listingId"]
        except Erreur as e:
            # 25002 « code JavaScript » sans rien de tel chez nous : vient de la fiche catalogue
            # eBay rattachée à l'EAN ; on republie une fois sans EAN
            if "25002" not in str(e) or "ean" not in produit:
                raise
            del produit["ean"]
            appel("PUT", f"/sell/inventory/v1/inventory_item/{sku}", {
                "product": produit, "condition": "NEW",
                "availability": {"shipToLocationAvailability": {"quantity": int(v["qte"])}},
                "packageWeightAndSize": {"weight": {"value": v["port"]["kg"], "unit": "KILOGRAM"}}})
            res["listing"] = appel("POST", f"/sell/inventory/v1/offer/{oid}/publish")["listingId"]
    return res


def main():
    global TOK
    essai = "--essai" in sys.argv
    TOK = jeton()
    if "--terminer-manuelles" in sys.argv or "--terminer-doublons" in sys.argv:
        terminer_manuelles(base.doc("ebay/annonces") or {}, "--terminer-doublons" in sys.argv)
        return
    if "--lister-manuelles" in sys.argv:
        print(f"{sum(1 for _ in (manuelles(base.doc('ebay/annonces') or {})))} sets en vente à la main")
        return
    a_faire = base.doc("ebay/a_publier") or {}
    faites = base.doc("ebay/annonces") or {}
    # Annonces retirées à la main sur eBay : notées « retire », jamais republiées ni mises à jour
    for s, v in faites.items():
        if v.get("listing") and not v.get("retire"):
            try:
                o = appel("GET", f"/sell/inventory/v1/offer/{v['offre']}", ok_404=True) or {}
            except Erreur:
                continue
            etat = (o.get("listing") or {}).get("listingStatus") or ""
            if not o or o.get("status") != "PUBLISHED" or etat in ("ENDED", "INACTIVE"):
                v.update(retire=True, retire_le=dt.date.today().isoformat(), par="vendeur")
    # Sets à retirer (encore moins chers chez LEGO, ou exclus à la main) : annonce retirée, offre gardée
    retirees = 0
    for s in base.doc("ebay/a_retirer") or []:
        v = faites.get(s)
        if v and v.get("listing") and not v.get("retire") and not essai:
            try:
                appel("POST", f"/sell/inventory/v1/offer/{v['offre']}/withdraw")
                v.update(retire=True, retire_le=dt.date.today().isoformat(), par="regle")
                retirees += 1
            except Erreur as e:
                JOURNAL.append(f"retrait {s} : {e}")
    a_faire = {s: v for s, v in a_faire.items() if not (faites.get(s) or {}).get("retire")}
    # Annonces en ligne dont le prix ou le port a changé : mises à jour ; nouvelles : PAR_JOUR au plus
    # (rythme choisi, et 150 annonces gratuites par mois sur eBay.fr)
    maj = {s: v for s, v in a_faire.items() if (faites.get(s) or {}).get("listing")
           and (faites[s].get("prix") != v["prix"] or faites[s].get("port") != v["port"]
                or faites[s].get("qte") != v["qte"] or faites[s].get("img", 1) != len(v["images"])
                or faites[s].get("desc", "?") != empreinte(v) or faites[s].get("plancher") != v.get("plancher"))}
    en_ligne = sum(1 for v in faites.values() if v.get("listing"))
    # Sets déjà en vente sur le compte par une annonce faite à la main : pas de seconde annonce
    deja = manuelles(faites)
    for s in [s for s in a_faire if s.split("-")[0] in deja and not (faites.get(s) or {}).get("listing")]:
        JOURNAL.append(f"{s} : déjà en vente à la main sur eBay, pas de nouvelle annonce")
        del a_faire[s]
    # Rythme réglable sans code : « par_jour » dans ebay/reglages (sinon EBAY_PAR_JOUR, 5 par défaut)
    par_jour = int((base.doc("ebay/reglages") or {}).get("par_jour") or PAR_JOUR)
    # Dans l'ordre choisi (figurines d'abord) : la base ne garde pas l'ordre des clés
    neuves = [s for s in sorted(a_faire, key=lambda s: a_faire[s].get("ordre", 1000)) if not (faites.get(s) or {}).get("listing")][:max(0, min(par_jour, MAX_EN_LIGNE - en_ligne))]
    a_faire = {s: a_faire[s] for s in list(maj) + neuves}
    if not a_faire:
        base.doc_ecrire("ebay/annonces", faites)
        print(f"rien à publier, {retirees} annonces retirées")
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
    print(f"{retirees} retirées, {ok} annonces traitées ({len(maj)} mises à jour, {len(neuves)} nouvelles{', essai' if essai else ''}), {err} erreurs")
    if err and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
