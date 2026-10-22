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
    if re.search(r"arriv[ée]+ en relais|arriv[ée]+ au point|est arriv[ée]e|disponible|pr[êe]t à être retir|à retirer|changement de votre point", s):
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
    # Numéro de commande : un numéro déjà connu (commande ou colis précédent) cité dans le mail
    for c in connus:
        n = c.get("num")
        if n and len(str(n)) >= 6 and re.search(r"(?<![A-Z0-9])" + re.escape(str(n)) + r"(?![0-9])", sujet + " " + p):
            e["num"] = n
            break
    if "num" not in e and e.get("suivi"):
        n = next((c.get("num") for c in connus if c.get("suivi") == e["suivi"] and c.get("num")), None)
        if n:
            e["num"] = n
    mag = magasin(adresse) or next((v for k, v in MAGASINS.items() if k.split(".")[0].upper() in sujet.upper()), None)
    if mag and mag != "LEGO.COM" or (mag == "LEGO.COM" and "lego" in adresse):
        e["magasin"] = mag
    tr = next((v for k, v in TRANSPORTEURS.items() if k in adresse or k in p.lower()[:2000]), None)
    if tr:
        e["transporteur"] = tr
    j = re.search(r"(?:jusqu'?au|avant le|au plus tard le)\s*(?:[a-zé]+\s+)?(\d{1,2})(?:/(\d{1,2})/|\s+([a-zéû]+)\s+)(\d{4})", p, re.I)
    if j and (j.group(2) or j.group(3).lower() in MOIS):
        e["jusqu"] = date_fr(j.group(1), j.group(2) or MOIS[j.group(3).lower()], j.group(4))
    if not (e.get("num") or e.get("suivi")):
        return None
    return e


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


def newsletter(adresse: str, sujet: str, t: str) -> dict | None:
    if not any(k in adresse for k in NEWSLETTERS):
        return None
    if re.search(r"commande|colis|facture|re[çc]u|livraison|cashback", sujet, re.I):
        return None
    enseigne = magasin(adresse) or adresse.split("@")[-1].split(".")[0].upper()
    p = plat(t)
    phrases = [x.strip() for x in re.split(r"(?<=[.!?|\n])\s+", sujet + ". " + t) if "lego" in x.lower()]
    op = next((plat(x)[:200] for x in phrases if re.search(OPERATION, x, re.I)), None)
    if "lego" not in p.lower() or not op:
        return {"pub": enseigne.title(), "lego": False}
    return {"pub": enseigne.title(), "lego": True, "op": op}


# ---------- Entrée d'un mail ----------

def lire(mail_id: str, expediteur: str, sujet: str, corps: str, jour: str, url: str, connus: list[dict]) -> dict | None:
    """Entrée pour mails.ajouter, ou None si le mail doit être lu par Claude."""
    t = texte(corps)
    adresse = origine(expediteur, sujet, t)
    base = {"mail": mail_id}
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
    if re.search(r"confirm", sujet, re.I) and magasin(adresse):
        return None  # commande d'un magasin pas (ou plus) lisible par les règles : pour Claude
    e = newsletter(adresse, sujet, t)
    if e:
        return {**base, **e}
    return None
