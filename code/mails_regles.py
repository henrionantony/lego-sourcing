"""Lecture des mails de la boîte dédiée par règles fixes (sans Claude).

Chaque mail donne UNE entrée pour mails.ajouter (même format que la lecture faite
par Claude), ou None quand les règles ne savent pas le lire avec certitude : il
reste alors « à lire » et le passage quotidien de Claude s'en occupe (et corrige
la règle si un magasin a changé son mail).

Les règles s'appuient sur ce qui bouge peu : l'expéditeur, les mots de l'objet,
le format des numéros de commande et de suivi. Une commande n'est acceptée que si
la somme de ses lignes retombe sur le total payé.
Jamais de nom, d'adresse ni de téléphone dans les entrées.
"""
import html
import re

# Expéditeurs (dans l'adresse) -> magasin, comme dans la base
MAGASINS = {"leclerc": "E.LECLERC", "carrefour": "CARREFOUR", "lego.com": "LEGO.COM", "amazon": "AMAZON.FR",
            "cdiscount": "CDISCOUNT", "fnac": "FNAC", "auchan": "AUCHAN", "kingjouet": "KING JOUET",
            "joueclub": "JOUECLUB", "cultura": "CULTURA", "smyths": "SMYTHS"}
TRANSPORTEURS = {"chronopost": "Chronopost", "pickup.fr": "Chronopost", "colissimo": "Colissimo", "laposte": "La Poste",
                 "mondialrelay": "Mondial Relay", "dpd": "DPD", "ups": "UPS", "gls": "GLS", "relaiscolis": "Relais Colis"}
IGNORES = ("leboncoin", "ebay", "ideas.lego.com", "sell.amazon", "google.com", "paypal", "vinted")
NEWSLETTERS = ("fnac", "email.carrefour", "e.crm.lego.com", "news.", "newsletter", "kingjouet", "joueclub",
               "cultura", "auchan", "smyths", "cdiscount", "amazon", "leclerc")


def texte(corps: str) -> str:
    """Texte à plat : sans liens, balises ni caractères invisibles ; cellules séparées par « | »."""
    t = re.sub(r"(?i)</t[dh]>", " | ", corps)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"\]\([^)]*\)|\(\s*https?://[^)]*\)|<https?://[^>]*>|https?://\S+", " ", t)
    t = re.sub(r"[͏﻿​‌‍­\xa0]", " ", t)
    t = re.sub(r"[*\[\]#]", " ", t)
    t = re.sub(r"(\|\s*)+\|", "|", t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def plat(t: str) -> str:
    return re.sub(r"\s+", " ", t)


def euros(x: str) -> float:
    return float(re.sub(r"[^\d,.]", "", x).replace(",", "."))


def date_fr(j: str, m: str, a: str) -> str:
    return f"{a}-{int(m):02d}-{int(j):02d}"


def origine(expediteur: str, sujet: str, t: str) -> str:
    """Adresse de l'expéditeur réel (les mails transférés par son filtre viennent de sa boîte perso)."""
    if sujet.lower().startswith(("fwd", "tr:", "fw:")):
        m = re.search(r"(?:De|From)\s*:[^\n]*?([\w.+-]+@[\w.-]+)", t)
        if m:
            return m.group(1).lower()
        # En-tête du transfert absent : l'enseigne la plus citée dans le mail
        compte = {k: len(re.findall(re.escape(nom), t, re.I)) for k, nom in
                  (("igraal.com", "igraal"), ("leclerc.fr", "leclerc"), ("carrefour.fr", "carrefour"),
                   ("lego.com", "lego®"), ("amazon.fr", "amazon"), ("cdiscount.com", "cdiscount"))}
        k = max(compte, key=compte.get)
        if compte[k]:
            return "transfert@" + k
    return expediteur.lower()


def magasin(adresse: str) -> str | None:
    return next((v for k, v in MAGASINS.items() if k in adresse), None)


# ---------- Commandes ----------

def commande_leclerc(t: str, p: str) -> dict | None:
    num = re.search(r"Num[ée]ro de commande\s*:?\s*([A-Z0-9]{8,12})\b", p)
    d = re.search(r"Date de commande\s*:?\s*(\d{2})/(\d{2})/(\d{4})", p)
    tot = re.search(r"Total\s*:?\s*([\d ]+,\d\d)\s*€", p)
    lignes = [{"set": s, "q": int(q), "pu": euros(pu)} for s, pu, q in re.findall(
        r"\b(\d{4,7})\s*\|?\s*Prix unitaire TTC\s*:?\s*([\d ]+,\d\d)\s*€\s*\|?\s*Quantit[ée]\s*:?\s*(\d+)", p)]
    if not (num and d and tot and lignes):
        return None
    return {"d": date_fr(*d.groups()), "magasin": "E.LECLERC", "num": num.group(1), "total": euros(tot.group(1)),
            "lignes": lignes}


def commande_carrefour(t: str, p: str, jour: str) -> dict | None:
    num = re.search(r"commande n[°o]\s*(\d{6,12})", p)
    tot = re.search(r"Montant pay[ée]\s*\|?\s*([\d ]+,\d\d)\s*€", p)
    lignes = [{"set": s, "q": int(q), "pu": euros(pu)} for s, q, pu in re.findall(
        r"\b(\d{4,7})\b[^|\n]{0,40}\|\s*(\d{1,2})\s*\|\s*([\d ]+,\d\d)\s*€\s*\|\s*[\d ]+,\d\d\s*€", p)]
    if not (num and tot and lignes):
        return None
    e = {"d": jour, "magasin": "CARREFOUR", "num": num.group(1), "total": euros(tot.group(1)), "lignes": lignes}
    cag = re.search(r"Montant cagnott[ée] sur votre carte\s*\|?\s*([\d ]+,\d\d)\s*€", p)
    if cag:
        e["cagnotte"] = euros(cag.group(1))
    return e


def commande(adresse: str, sujet: str, t: str, jour: str) -> dict | None:
    if not re.search(r"confirm|commande (?:est )?(?:bien )?(?:re[çc]ue|enregistr)", sujet, re.I):
        return None
    p = plat(t)
    e = None
    if "leclerc" in adresse:
        e = commande_leclerc(t, p)
    elif "carrefour" in adresse:
        e = commande_carrefour(t, p, jour)
    if not e:
        return None
    # Contrôle : les lignes doivent retomber sur le total, sinon on ne devine pas
    if abs(sum(x["q"] * x["pu"] for x in e["lignes"]) - e["total"]) > 0.05:
        return None
    return e


# ---------- Colis ----------

SUIVI = r"(?<![A-Z0-9])([A-Z]{2}\d{9}[A-Z]{2}|\d{14}|[A-Z]{2}\d{11})(?![0-9])"
MOIS = {m: i for i, m in enumerate(["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                                    "septembre", "octobre", "novembre", "décembre"], 1)}


def etape(sujet: str, p: str) -> str | None:
    s = sujet.lower()
    tout = (s + " " + p[:1500].lower())
    relais = re.search(r"relais|point de retrait|pickup|consigne|bureau de poste|magasin", tout)
    if re.search(r"arriv[ée]+ en relais|arriv[ée]+ au point|est arriv[ée]e|disponible|pr[êe]t à être retir|à retirer|pour retirer|vous attend|changement de votre point", s):
        return "a_retirer"
    if re.search(r"a été livr[ée]|est livr[ée]|livraison effectu", s):
        return "a_retirer" if relais else "livre"
    if re.search(r"en route|en chemin|exp[ée]di|en cours de livraison|est prête|pris en charge|parti", s):
        return "expedie"
    return None


def colis(adresse: str, sujet: str, t: str, jour: str, connus: list[dict]) -> dict | None:
    if not (magasin(adresse) or any(k in adresse for k in TRANSPORTEURS) or "shipup" in adresse):
        return None
    p = plat(t)
    et = etape(sujet, p)
    if not et:
        return None
    e = {"colis": et, "d": jour, "sujet": re.sub(r"^(?:fwd|tr|fw)\s*:\s*", "", sujet, flags=re.I)[:150]}
    m = re.search(SUIVI, sujet + " " + p)
    if m:
        e["suivi"] = m.group(1)
    n = num_commande(sujet, p)
    if n:
        e["num"] = n
    # Numéro de commande : un numéro déjà connu (commande ou colis précédent) cité dans le mail
    for c in ([] if "num" in e else connus):
        n = c.get("num")
        if n and len(str(n)) >= 6 and re.search(r"(?<![A-Z0-9])" + re.escape(str(n)) + r"(?![0-9])", sujet + " " + p):
            e["num"] = n
            break
    if "num" not in e and e.get("suivi"):
        n = next((c.get("num") for c in connus if c.get("suivi") == e["suivi"] and c.get("num")), None)
        if n:
            e["num"] = n
    # Les mails du transporteur (Chronopost, Pickup) ne citent pas le magasin : on le reprend
    # d'un mail déjà lu du même colis ou de la même commande
    meme = [c for c in connus if (e.get("suivi") and c.get("suivi") == e["suivi"]) or (e.get("num") and str(c.get("num")) == str(e["num"]))]
    if "num" not in e:
        n = next((c.get("num") for c in meme if c.get("num")), None)
        if n:
            e["num"] = n
    mag = magasin(adresse) or next((v for k, v in MAGASINS.items() if k.split(".")[0].upper() in sujet.upper()), None) \
        or next((c.get("magasin") for c in meme if c.get("magasin")), None)
    if mag and mag != "LEGO.COM" or (mag == "LEGO.COM" and "lego" in adresse):
        e["magasin"] = mag
    tr = next((v for k, v in TRANSPORTEURS.items() if k in adresse or k in p.lower()[:2000]), None)
    if tr:
        e["transporteur"] = tr
    j = re.search(r"(?:jusqu['’]?au|avant le|au plus tard le)\s*(?:[a-zé]+\s+)?(\d{1,2})(?:/(\d{1,2})/|\s+([a-zéû]+)\s+)(\d{4})", p, re.I)
    if j and (j.group(2) or j.group(3).lower() in MOIS):
        e["jusqu"] = date_fr(j.group(1), j.group(2) or MOIS[j.group(3).lower()], j.group(4))
    l = lieu(p)
    if l:
        e["lieu"] = l
    # « Livré » sans mot de relais dans le mail, mais la même commande allait en point de retrait : à retirer
    if et == "livre" and (l or any(c.get("lieu") or c.get("colis") == "a_retirer" for c in meme)):
        e["colis"] = "a_retirer"
    if not (e.get("num") or e.get("suivi")):
        return None
    return e


NUM_COMMANDE = [r"commande\s*LEGO\s*(T\d{9})\b",
                r"(?:N[º°o]\.?|num[ée]ro)\s*(?:de\s*)?commande\s*:?\s*([A-Z0-9]{6,14}?)(?=$|[^A-Za-z0-9]|[A-Z][a-z])",
                r"commande\s*n[º°o]\s*:?\s*(\d{6,14})\b"]


def num_commande(sujet: str, p: str) -> str | None:
    """Numéro de commande du marchand écrit dans le mail (jamais un numéro de suivi)."""
    for rx in NUM_COMMANDE:
        m = re.search(rx, sujet + " " + p, re.I if rx.startswith("commande\\s*n") else 0)
        if m and not re.fullmatch(SUIVI, m.group(1)):
            return m.group(1)
    return None


# Nom du point de retrait (jamais une adresse de domicile : seulement un nom qui contient un mot de magasin ou de relais)
LIEU = [r"Adresse de point relais\s*:\s*([^|\d]{3,60})",
        r"Livraison en point relais\s*\|\s*([^|\d]{3,60})",
        r"Détails du relais Pickup\s*\|\s*([^|\d]{3,60})",
        r"Votre point de retrait [^|]{0,20}\|\s*([^|\d]{3,60})",
        r"(?:dans votre|au) point de retrait\s+([A-Z][A-Z .'’-]{3,50}?)\s+(?:jusqu|avant|\.|$)",
        r"Adresse de livraison\s*([^|\d]{3,60})"]
MOTS_LIEU = r"LECLERC|CARREFOUR|LA POSTE|BUREAU|RELAIS|PICKUP|CONSIGNE|LOCKER|TABAC|PRESSE|INFORMATIQUE|ATELIER|BOUTIQUE|MAGASIN|HYPER|SUPER|SHOP"


def lieu(p: str) -> str | None:
    for rx in LIEU:
        m = re.search(rx, p)
        if not m:
            continue
        nom = re.split(r"\s+(?:ESPACE|BOULEVARD|AVENUE|RUE|PLACE|CENTRE|BD|AV)\b|\s*(?:Date|SOUS-TOTAL|Hyper)\b", m.group(1).strip(" :|"))[0].strip(" ,.-")
        if re.search(MOTS_LIEU, nom, re.I) and 3 <= len(nom) <= 60:
            mots = nom.lower().split()
            return " ".join(w if i and w in ("de", "la", "du", "des", "le", "les", "et") else
                            "E." + w[2:].capitalize() if w.startswith("e.") else w.capitalize() for i, w in enumerate(mots))
    return None


# ---------- iGraal ----------

def igraal(adresse: str, sujet: str, t: str) -> dict | None:
    if "igraal" not in adresse:
        return None
    p = plat(t)
    m = re.search(r"cashback de\s*([\d ]+,\d\d)\s*€\s*,?\s*suite à votre achat sur\s*(.+?)\s+le\s+(\d{2})/(\d{2})/(\d{4})", p)
    if not m:
        return None
    statut = "attente" if re.search(r"en attente", sujet + " " + p, re.I) and not re.search(r"valid|vers[ée]|pay[ée]", sujet, re.I) else "valide"
    return {"igraal": statut, "d": date_fr(*m.groups()[2:]), "marchand": m.group(2)[:60], "montant": euros(m.group(1))}


# ---------- Newsletters ----------

OPERATION = r"-\s*\d{2}\s*%|\d{2}\s*%\s*(?:de remise|cagnott|offert|sur)|cagnott|points? doubl|offert|2e à|2ème à|deuxième à"


def nom_enseigne(e: str) -> str:
    return "LEGO.COM" if e.startswith("LEGO") else e.title()


def newsletter(adresse: str, sujet: str, t: str) -> dict | None:
    if not any(k in adresse for k in NEWSLETTERS):
        return None
    if re.search(r"commande|colis|facture|re[çc]u|livraison|cashback", sujet, re.I):
        return None
    enseigne = (magasin(adresse) or adresse.split("@")[-1].split(".")[0].upper()).replace(".FR", "")
    p = plat(t)
    phrases = [x.strip() for x in re.split(r"(?<=[.!?|\n])\s+", sujet + ". " + t) if "lego" in x.lower()]
    op = next((plat(x)[:200] for x in phrases if re.search(OPERATION, x, re.I)), None)
    if "lego" not in p.lower() or not op:
        return {"pub": nom_enseigne(enseigne), "lego": False}
    return {"pub": nom_enseigne(enseigne), "lego": True, "op": op}


# ---------- Leboncoin : annonce en ligne / supprimée ----------

def leboncoin(adresse: str, sujet: str, t: str, corps: str) -> dict | None:
    """{"lbc": "en_ligne"|"supprimee", "set", "titre", "url"} ; le pseudo du compte n'est jamais gardé."""
    if "leboncoin" not in adresse:
        return None
    p = plat(t)
    c = re.search(r"nouveau contact pour\s*[«\"“](.+?)[»\"”]", sujet)
    if c:
        # Contact d'un acheteur (favori, message) : compté par set pour mesurer l'effet des republications
        nums = re.findall(r"(?<!\d)(\d{4,7})(?!\d)", c.group(1))
        return {"lbc": "contact", "set": nums[-1], "titre": c.group(1)[:200]} if nums else None
    m = re.search(r"Votre annonce\s*[«\"](.+?)[»\"]\s*(?:vient d.être validée|est en ligne)", sujet + " " + p)
    if m:
        etat = "en_ligne"
    else:
        m = re.search(r"Votre annonce ((?:(?!Votre annonce).)+?) a bien été supprimée", p)
        etat = "supprimee"
    if not m:
        return None
    titre = re.sub(r"\s+", " ", m.group(1)).strip(" «»\"")
    nums = re.findall(r"(?<!\d)(\d{4,7})(?!\d)", titre)
    if not nums:
        return None  # sans numéro de set : pour Claude
    u = re.search(r"https://www\.leboncoin\.fr/ad/[a-z_]+/\d+", corps) if etat == "en_ligne" else None
    return {"lbc": etat, "set": nums[-1], "titre": titre[:200], **({"url": u.group(0)} if u else {})}


# ---------- Entrée d'un mail ----------

def lire(mail_id: str, expediteur: str, sujet: str, corps: str, jour: str, url: str, connus: list[dict]) -> dict | None:
    """Entrée pour mails.ajouter, ou None si le mail doit être lu par Claude."""
    t = texte(corps)
    adresse = origine(expediteur, sujet, t)
    base = {"mail": mail_id}
    e = leboncoin(adresse, sujet, t, corps)
    if e:
        return {**base, **e, "d": jour}
    if "vinted" in adresse and re.search(r"vendu|vente|achet|commande|bordereau|envo", sujet, re.I):
        return None  # vente Vinted : pour Claude (enregistrement de la vente)
    if any(k in adresse for k in IGNORES):
        return {**base, "ignore": True}
    if re.search(r"facture|re[çc]u lego", sujet, re.I) and magasin(adresse):
        return {**base, "ignore": True}
    for e in (commande(adresse, sujet, t, jour), igraal(adresse, sujet, t),
              colis(adresse, sujet, t, jour, connus)):
        if e:
            if "colis" in e:
                e["url"] = url
            return {**base, **e}
    if re.search(r"confirm|annul|rembours|retour", sujet, re.I) and magasin(adresse):
        return None  # commande d'un magasin pas (ou plus) lisible par les règles, annulation, remboursement : pour Claude
    if re.search(r"code (?:à usage unique|d.authentification|de (?:vérification|connexion|sécurité))|votre avis|satisfaction|enquête|mot de passe", sujet, re.I):
        return {**base, "ignore": True}
    e = newsletter(adresse, sujet, t)
    if e:
        return {**base, **e}
    return None
