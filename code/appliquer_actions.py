"""Applique dans la base les gestes faits dans l'application centrale
(« Received », « Paid », « I bought it »). La session horaire lit la collection
« actions » de l'application, l'écrit dans un fichier JSON, lance ce script,
puis marque comme traitées les actions qu'il renvoie.

Usage : python3 appliquer_actions.py actions.json   → affiche les ids traités (JSON)
"""
import datetime as dt
import re
import json
import sys
from pathlib import Path

import base
import figs_sets
from regles import PRELEVEMENTS
from ventes_net import net


REVERSEE = "Cagnotte reversée perso → pro"
DEBUT_TRESO = "2026-10-18"  # la trésorerie suit les mouvements depuis cette date


def caisse(le, montant, libelle, **lien):
    """Mouvement de trésorerie, pour que la caisse suive les achats et ventes."""
    # Un rattrapage (vente ou achat daté d'avant le départ de la trésorerie) est déjà
    # compris dans le montant de départ : il ne bouge pas la caisse.
    if str(le)[:10] < DEBUT_TRESO:
        return
    base.ajouter("tresorerie", [{"le": str(le)[:10], "type": "achat" if montant < 0 else "vente",
                                  "montant": round(montant, 2), "libelle": libelle[:200],
                                  "achat_id": lien.get("achat_id"), "vente_id": lien.get("vente_id"), "a_rendre_le": None}])


def recu(a):
    le = f"reçu le {a.get('le', dt.date.today().isoformat())[:10]}"
    for x in base.lire("articles", f"select=id,notes&achat_id=eq.{int(a['achat_id'])}&notes=ilike.*livraison*"):
        # garde le début de la note (ex. points Insiders), remplace seulement « en livraison »
        n = re.sub(r"en livraison", le, str(x["notes"]), flags=re.I) if x["notes"].lower() != "en livraison" else le
        base.modifier("articles", f"id=eq.{x['id']}", {"notes": n, "livre": True})


def cashback_recu(a):
    valeurs = {"cashback_recu": True}
    if colonne_statut():
        valeurs["cashback_statut"] = "payé"
    base.modifier("achats", f"id=eq.{int(a['achat_id'])}", valeurs)


def colonne_statut() -> bool:
    """La colonne achats.cashback_statut existe-t-elle (ajoutée à la main dans Supabase) ?"""
    try:
        base.lire("achats", "select=cashback_statut&limit=1")
        return True
    except Exception:
        return False


def cashback_confirme(a):
    """iGraal a enregistré le cashback : il passe « en attente » (montant éventuellement corrigé)."""
    aid = int(a["achat_id"])
    valeurs = {"cashback": float(a["montant"])} if a.get("montant") else {}
    if colonne_statut():
        valeurs["cashback_statut"] = "en attente"
    else:
        notes = (base.lire("achats", f"select=notes&id=eq.{aid}") or [{}])[0].get("notes") or ""
        valeurs["notes"] = (notes + f" Cashback confirmé le {str(a.get('le', dt.date.today().isoformat()))[:10]}.").strip()
    base.modifier("achats", f"id=eq.{aid}", valeurs)


def magasin_du_titre(titre: str) -> str:
    """Les titres Dealabs finissent par le marchand entre parenthèses : « … (Carrefour) »."""
    m = re.search(r"\(([^()]{2,40})\)\s*$", titre or "")
    return m.group(1) if m else "DEALABS"


def achete(a):
    """Un deal acheté : une commande (ou le panier en cours) et une ligne d'article."""
    d = base.lire("deals", f"select=*&id=eq.{int(a['deal_id'])}")
    if not d:
        raise ValueError(f"deal {a['deal_id']} introuvable")
    d = d[0]
    # Cagnottage (« 15.98€ sur la carte de fidélité », « 12 € cagnottés ») : le prix du
    # deal est déjà net de la cagnotte, comme dans le fichier d'achats ; on la note à part.
    # Le formulaire de l'application la donne par article ; sinon on la lit dans le titre.
    if a.get("cagnotte") is None:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*€\s*(?:sur (?:la|ta|votre) carte|cagnott|de cagnotte)", d["titre"], re.I)
        a = {**a, "cagnotte": float(m.group(1).replace(",", ".")) if m else 0}
    enregistrer_achat(a, id_set=d.get("id_set"), designation=d["titre"], prix_unite=float(a.get("prix") or d["prix"]),
                      lieu=a.get("lieu") or magasin_du_titre(d["titre"]), note=f"{d['titre'][:200]} ({d['lien']})")
    base.modifier("deals", f"id=eq.{d['id']}", {"achete": True})


def achat_libre(a):
    """Un article acheté sans deal dans l'application (ajouté au panier, ou nouvelle commande)."""
    if a.get("genre") in ("figlot", "autre"):
        # Lot de figurines ou autre article sans numéro de set : décrit à la saisie
        d = re.sub(r"\s+", " ", str(a.get("designation") or "")).strip()[:250]
        if not d:
            raise ValueError("description manquante")
        lot = a["genre"] == "figlot"
        enregistrer_achat(a, id_set=None, designation=d.upper(), prix_unite=float(a["prix"]),
                          lieu=str(a.get("lieu") or "AUTRE"), note=f"{d} (sans deal)",
                          categorie="LOT MINIFIGURES" if lot else "AUTRE", details_etat=None,
                          strategie="LOT/DETAILS" if lot else None)
        return
    s = re.sub(r"\D", "", str(a.get("set") or ""))[:10]
    if not s:
        raise ValueError("numéro de set manquant")
    nom = nom_du_set(s)
    # Stratégie de vente choisie à la saisie : set entier ou découpé (figurines et construction à part)
    strat = str(a.get("strategie") or "").upper()
    enregistrer_achat(a, id_set=s, designation=f"LEGO {nom} - {s}" if nom else f"LEGO {s}", prix_unite=float(a["prix"]),
                      lieu=str(a.get("lieu") or "AUTRE"), note=f"{nom or 'set'} {s} (sans deal)",
                      strategie=strat if strat in ("ENTIER", "LOT/DETAILS") else None)


def nom_du_set(s: str) -> str | None:
    import csv
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent / "data" / "referentiel.csv"
    with open(f, newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("base") == s:
                return r.get("name") or None
    return None


def enregistrer_achat(a, id_set, designation, prix_unite, lieu, note, categorie="SET", details_etat="SCELLE", strategie=None):
    q = int(a.get("quantite") or 1)
    cagnotte = round(float(a.get("cagnotte") or 0) * q, 2) or None
    # Date choisie dans le formulaire (achat de rattrapage), sinon le jour de la saisie
    jour, lieu = str(a.get("date") or a.get("le") or dt.date.today().isoformat())[:10], str(lieu).upper()[:60]
    retro = jour < DEBUT_TRESO  # rattrapage : déjà reçu, déjà payé (compris dans la trésorerie de départ)
    # Un panier : l'application donne une clé commune aux articles payés dans la même
    # transaction ; ils forment une seule commande. Sans clé, une commande par article.
    panier = re.sub(r"[^A-Za-z0-9_-]", "", str(a.get("panier") or ""))[:40]
    r = base.lire("achats", f"select=id,notes,cashback&notes=like.*%5Bpanier%20{panier}%5D*&limit=1") if panier else []
    cree = not r
    if r:
        base.modifier("achats", f"id=eq.{r[0]['id']}", {"notes": (r[0]["notes"] + " + " + note)[:2000]})
    else:
        r = base._appel("POST", "achats", [{"date_achat": jour, "lieu": lieu,
                                             "notes": f"Depuis l'application : {note}." + (" Rattrapage." if retro else " En livraison.") + (f" [panier {panier}]" if panier else "")}],
                        "return=representation")
    if not r:
        raise RuntimeError("commande non créée")
    # Points Insiders dépensés (750 pts = 5 €) : le coût reste le prix payé en euros,
    # la valeur des points est notée à part, comme dans l'Excel d'origine
    pts = int(float(a.get("points") or 0))
    note_art = "rattrapage" if retro else "en livraison"
    if pts > 0:
        val = f"{pts / 150:.2f}".replace(".", ",").removesuffix(",00")
        note_art = (f"PAYÉ {'' if prix_unite == 0 else 'EN PARTIE '}AVEC LES POINTS INSIDERS SUR LEGO.COM "
                    f"({pts} PTS = {val}€) - {note_art}")
    ok = base._appel("POST", "articles", [{"achat_id": r[0]["id"], "quantite": q, "categorie": categorie, "id_set": id_set,
                                           "designation": str(designation)[:300], "etat": "NEUF", "details_etat": details_etat,
                                           **({"strategie": strategie} if strategie else {}),
                                           "prix_achat": round(prix_unite * q, 2),  # prix_total est calculé par la base
                                           "offert": prix_unite == 0,  # boîte offerte (cadeau Lego.com…)
                                           "cagnotte": cagnotte,
                                           "statut": "à traiter", "livre": retro, "notes": note_art,
                                           **({"points_utilises": pts} if pts > 0 else {})}], "return=minimal")
    if ok is None:
        if cree:
            base._appel("DELETE", f"achats?id=eq.{r[0]['id']}")
        raise RuntimeError("article non créé")
    gagnes = int(float(a.get("points_gagnes") or 0))
    if gagnes > 0:  # points Insiders gagnés sur l'achat : notés, jamais déduits du prix
        deja = int(float((base.lire("achats", f"select=points_gagnes&id=eq.{r[0]['id']}") or [{}])[0].get("points_gagnes") or 0))
        base.modifier("achats", f"id=eq.{r[0]['id']}", {"points_gagnes": deja + gagnes})
    remarque = re.sub(r"\s+", " ", str(a.get("notes") or "")).strip()[:300]
    # Rattrapage dont la cagnotte n'a jamais été reversée : elle reste dans « Money to move »
    tag = " [cagnotte à reverser]" if retro and cagnotte and a.get("cagnotte_a_reverser") else ""
    if remarque or tag:  # note libre sur la transaction (ex. achetée par quelqu'un d'autre)
        n = (base.lire("achats", f"select=notes&id=eq.{r[0]['id']}") or [{}])[0].get("notes") or ""
        if tag in n:
            tag = ""
        base.modifier("achats", f"id=eq.{r[0]['id']}", {"notes": (n + (f" Note : {remarque}" if remarque else "") + tag)[:2000]})
    if float(a.get("cashback_pct") or 0) > 0:
        # iGraal paie un pourcentage du prix payé en caisse (cagnotte comprise)
        deja = float((base.lire("achats", f"select=cashback&id=eq.{r[0]['id']}") or [{}])[0].get("cashback") or 0)
        base.modifier("achats", f"id=eq.{r[0]['id']}",
                      {"cashback": round(deja + (prix_unite * q + (cagnotte or 0)) * float(a["cashback_pct"]) / 100, 2),
                       "cashback_recu": False})
    # Le compte pro paie le prix en rayon ; la cagnotte part sur la carte perso et
    # elle est reversée ensuite (carte « Money to move » dans To do).
    caisse(jour, -(prix_unite * q + (cagnotte or 0)), f"Achat : {designation}",
           achat_id=r[0]["id"])


def vire(a):
    """Cagnotte reversée du compte perso vers le compte pro."""
    caisse(a.get("le", dt.date.today().isoformat()), float(a["montant"]), REVERSEE + f" (commande {int(a['achat_id'])})",
           achat_id=int(a["achat_id"]))


def article_de(a) -> int | None:
    """Article d'origine d'une vente saisie dans l'application : directement pour
    une pièce de set découpé (« d<article>-… »), sinon le plus ancien article
    encore en stock pour ce numéro de set, le moins cher d'abord (on
    vend d'abord les exemplaires payés le moins cher). None si on ne le retrouve pas."""
    if str(a.get("origine") or "").isdigit():
        return int(a["origine"])
    item = str(a.get("item") or "")
    if item.startswith("d") and item[1:].split("-")[0].isdigit():
        return int(item[1:].split("-")[0])
    if str(a.get("set") or "").isdigit():
        st = base.lire("stock", f"select=*&reste=gt.0&id_set=like.{a['set']}*&order=date_achat.asc&limit=50")
        st = [x for x in st if str(x["id_set"]).split("-")[0].removesuffix(".0") == str(a["set"])]
        if st:
            return min(st, key=cout_unitaire)["id"]
    return None


def cout_unitaire(x) -> float:
    """Ce que l'exemplaire m'a coûté (part d'associé ramenée à 100 %, cadeau = 0)."""
    if x.get("offert"):
        return 0.0
    # prix_achat est le prix de la ligne entière (quantite exemplaires)
    return float(x.get("prix_achat") or 0) / (float(x.get("quote_part") or 1) or 1) / max(1, int(x.get("quantite") or 1))


def article_figurine(article_id, achat_id) -> int | None:
    """Ligne d'inventaire d'où vient une figurine vendue. L'ID Achat (ligne) prime ;
    avec seulement le Lot ID (achat), on prend la ligne de lot de figurines de cet achat,
    ou sa seule ligne. None si rien ne correspond (vente gardée, origine inconnue)."""
    if str(article_id or "").isdigit():
        if base.lire("articles", f"select=id&id=eq.{int(article_id)}"):
            return int(article_id)
    if str(achat_id or "").isdigit():
        arts = base.lire("articles", f"select=id,categorie,strategie&achat_id=eq.{int(achat_id)}&order=id.asc")
        lots = [x for x in arts if str(x.get("categorie") or "") in ("LOT MINIFIGURES", "MINIFIGS", "COMICS", "LEGO COMICS", "LOT COMICS")
                or "LOT" in str(x.get("strategie") or "")]
        if len(lots) >= 1:
            return lots[0]["id"]
        if len(arts) == 1:
            return arts[0]["id"]
    return None


def article_set(article_id, achat_id, num) -> int | None:
    """Ligne d'inventaire d'un set vendu saisi à la main : l'ID Achat (ligne) prime ;
    avec le Lot ID (achat), la ligne de ce set dans l'achat, ou sa seule ligne ;
    sinon l'exemplaire en stock le moins cher. None si rien ne correspond."""
    if str(article_id or "").isdigit():
        if base.lire("articles", f"select=id&id=eq.{int(article_id)}"):
            return int(article_id)
    if str(achat_id or "").isdigit():
        arts = base.lire("articles", f"select=id,id_set&achat_id=eq.{int(achat_id)}&order=id.asc")
        meme = [x for x in arts if num and str(x.get("id_set") or "").split("-")[0].removesuffix(".0") == str(num)]
        if meme:
            return meme[0]["id"]
        if len(arts) == 1:
            return arts[0]["id"]
    return article_de({"set": num}) if num else None


def vente_lot(a):
    """Vente d'un lot scannée dans l'app : une ligne de vente par article, le prix
    total réparti par l'app (au prix public Lego), chaque article déjà choisi
    (exemplaire le moins cher par défaut). Un seul mouvement de trésorerie."""
    lignes = a.get("lignes") or []  # article_id vide : set absent de la base, vente gardée quand même
    if not lignes:
        raise ValueError("lot vide")
    total, n = float(a["total"]), len(lignes)
    figs = a.get("genre") == "fig"  # saisie « Minifig » : chaque ligne donne son ID Achat et/ou son Lot ID
    saisie = a.get("genre") in ("fig", "set")  # saisie à la main (sinon colis scanné, articles déjà choisis)
    for l in lignes:
        if figs:
            l["article_id"] = article_figurine(l.get("article_id"), l.get("achat_id"))
        elif saisie:
            l["article_id"] = article_set(l.get("article_id"), l.get("achat_id"), l.get("set"))
    d = str(a.get("date") or a.get("le"))[:10]
    source = "saisie application" if saisie else "scan application"
    note = f"Lot de {n} article{'s' if n > 1 else ''}, {total:.2f} € au total ({source})" if n > 1 else source.capitalize()
    # Plusieurs articles vendus ensemble : même numéro de commande de vente, comme dans l'Excel
    cmd = None
    if n > 1:
        der = base.lire("ventes", "select=commande_id&commande_id=not.is.null&order=commande_id.desc&limit=1")
        cmd = int(der[0]["commande_id"]) + 1 if der else 1
    faites = base._appel("POST", "ventes", [{"article_id": int(l["article_id"]) if l.get("article_id") else None, "date_vente": d,
                             "plateforme": str(a.get("plateforme") or "").upper()[:40],
                             "produit": "FIGURINE" if (figs or l.get("fig")) else "SET", "id_minifig": l.get("fig") or None,
                             "nom_article": str(l.get("titre") or l.get("set") or l.get("fig") or "")[:300], "quantite": 1,
                             "prix_vente": round(float(l["prix"]), 2),
                             "revenu_net": net(l["prix"]),
                             "commande_id": cmd,
                             "notes": note + ("" if l.get("article_id") else " (origine inconnue)")} for l in lignes],
                         "return=representation") or []
    for v in faites:
        figurine_vendue(v)
    caisse(d, total * (1 - PRELEVEMENTS), f"Vente lot ({n}) : " + ", ".join(str(l.get("set") or l.get("fig") or l.get("titre")) for l in lignes))


def figurine_vendue(v: dict):
    """Figurine vendue : la ligne de la table figurines (set découpé, lot) est cochée ;
    sans origine connue, la vente est rattachée à l'achat de cette figurine."""
    if not v.get("id_minifig"):
        return
    art = figs_sets.vendre(v["id"], v["id_minifig"], v.get("article_id"))
    if art and not v.get("article_id"):
        base.modifier("ventes", f"id=eq.{v['id']}", {"article_id": art, "notes": str(v.get("notes") or "").replace(" (origine inconnue)", "")})


def horizon(a):
    """Mission « derniers achats » : poster tout de suite (Rotation) ou garder jusqu'à la retraite (Retirement)."""
    h = a.get("horizon")
    if h not in ("Rotation", "Retirement"):
        raise ValueError("horizon inconnu")
    ids = ",".join(str(int(i)) for i in a.get("article_ids") or [])
    if not ids:
        return
    vise = str(a.get("vise_le") or "")[:10] if h == "Retirement" else None
    if vise and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", vise):
        vise = None
    base.modifier("articles", f"id=in.({ids})", {"horizon": h, "vise_le": vise})


def figs_lier(a):
    """« I have them all » de l'onglet Part-out : figurines rattachées à l'article."""
    figs_sets.lier(int(a["article_id"]), a.get("figs") or [])


def vente(a):
    art = article_de(a)
    v = base._appel("POST", "ventes", [{"article_id": art, "date_vente": str(a.get("date") or a.get("le"))[:10],
                                        "plateforme": str(a.get("plateforme") or "").upper()[:40],
                                        "produit": "FIGURINE" if a.get("fig") else "SET",
                                        "nom_article": str(a.get("titre") or "")[:300], "quantite": 1,
                                        "prix_vente": float(a["prix"]), "id_minifig": a.get("fig") or None,
                                        "port_paye": float(a["port"]) if a.get("port") else None,
                                        "revenu_net": net(a["prix"], a.get("port")),
                                        "notes": "Saisie depuis l'application" + ("" if art else " (origine inconnue)")}],
                    "return=representation")
    if v:
        figurine_vendue(v[0])
    # Encaissé net des prélèvements, mis de côté pour l'URSSAF
    caisse(a.get("date") or a.get("le"), float(a["prix"]) * (1 - PRELEVEMENTS), f"Vente : {a.get('titre') or ''}",
           vente_id=v[0]["id"] if v else None)


# Corrections faites depuis l'onglet History : seuls ces champs sont modifiables.
CORRIGIBLES = {
    "ventes": {"date_vente": "date", "prix_vente": "prix", "quantite": "entier", "plateforme": "MAJ", "nom_article": "texte"},
    "articles": {"quantite": "entier", "prix_total": "prix", "cagnotte": "prix", "designation": "texte", "notes": "texte"},
    "achats": {"date_achat": "date", "lieu": "MAJ", "notes": "texte", "cashback": "montant"},
}


def _valeur(genre, v):
    if v in (None, ""):
        return 0.0 if genre == "montant" else None
    if genre == "date":
        return dt.date.fromisoformat(str(v)[:10]).isoformat()
    if genre in ("prix", "montant"):
        return round(float(str(v).replace(",", ".")), 2)
    if genre == "entier":
        return max(1, int(float(v)))
    return str(v).strip()[:300].upper() if genre == "MAJ" else str(v).strip()[:1000]


def corrige(a):
    """Correction d'une vente, d'un article ou d'une commande ; la trésorerie suit
    l'écart de prix pour les lignes datées depuis qu'elle est tenue."""
    table, ident = a["table"], int(a["id"])
    champs = CORRIGIBLES[table]
    valeurs = {k: _valeur(champs[k], v) for k, v in (a.get("valeurs") or {}).items() if k in champs}
    if not valeurs:
        return
    avant = (base.lire(table, f"select=*&id=eq.{ident}") or [None])[0]
    if avant is None:
        raise ValueError(f"{table} {ident} introuvable")
    if table == "articles" and "prix_total" in valeurs:
        valeurs["prix_achat"] = valeurs.pop("prix_total")  # prix_total est calculé par la base
    if base.modifier(table, f"id=eq.{ident}", valeurs) is None:
        raise RuntimeError(f"{table} {ident} non modifié")
    le = a.get("le", dt.date.today().isoformat())
    if table == "ventes" and "prix_vente" in valeurs and str(avant.get("date_vente") or "") >= DEBUT_TRESO:
        ecart = (valeurs["prix_vente"] or 0) - float(avant.get("prix_vente") or 0)
        if ecart:
            caisse(le, ecart * (1 - PRELEVEMENTS), f"Correction vente {ident} : {avant.get('nom_article') or ''}")
    if table == "articles" and ("prix_achat" in valeurs or "cagnotte" in valeurs):
        achat = (base.lire("achats", f"select=date_achat&id=eq.{avant['achat_id']}") or [{}])[0]
        if str(achat.get("date_achat") or "") >= DEBUT_TRESO:
            paye = lambda x: float(x.get("prix_achat") or 0) + float(x.get("cagnotte") or 0)
            ecart = paye({**avant, **valeurs}) - paye(avant)
            if ecart:
                caisse(le, -ecart, f"Correction achat {avant['achat_id']} : {avant.get('designation') or ''}",
                       achat_id=avant["achat_id"])


def colis_lien(a):
    """Rattache un mail de colis à une commande (choisie dans l'application), ou l'écarte (achat_id 0)."""
    import mails
    tout = mails.tout()
    for c in tout:
        if c.get("mail") == str(a["mail"]) and c.get("colis"):
            if int(a["achat_id"]):
                c["achat_id"] = int(a["achat_id"])
            else:
                c["ecarte"] = True
            break
    else:
        raise ValueError(f"mail {a['mail']} inconnu")
    base.json_partage_ecrire("commandes", mails.FICHIER, tout)


def perte(a):
    """Article perdu ou donné, constaté à l'inventaire : il sort du stock comme une vente à 0 €,
    sans mouvement de trésorerie."""
    art = article_de(a)
    base.ajouter("ventes", [{"article_id": art, "date_vente": str(a.get("le"))[:10], "plateforme": "PERTE",
                             "produit": "SET", "nom_article": str(a.get("titre") or "")[:300], "quantite": 1,
                             "prix_vente": 0, "statut": "perte",
                             "notes": "Perdu ou donné, constaté à l'inventaire" + ("" if art else " (origine inconnue)")}])


def supprime(a):
    """Commande annulée (achat ou vente), effacée depuis l'onglet History comme si elle
    n'avait jamais eu lieu : avec tout ce qui en dépend (articles, cagnotte, cashback,
    mouvements de trésorerie, figurines)."""
    ident = int(a["id_ligne"])
    if a["table"] == "achats":
        achat = base.lire("achats", f"select=id,notes&id=eq.{ident}")
        if not achat:
            return  # déjà effacé
        arts = base.lire("articles", f"select=id,id_set&achat_id=eq.{ident}")
        ids = ",".join(str(x["id"]) for x in arts)
        # Ventes déjà rattachées à ces exemplaires : rattachées à un autre exemplaire en stock
        vendus = base.lire("ventes", f"select=id,article_id&article_id=in.({ids})") if ids else []
        for t, f in (("tresorerie", f"achat_id=eq.{ident}"), ("articles", f"achat_id=eq.{ident}"), ("achats", f"id=eq.{ident}")):
            if base._appel("DELETE", f"{t}?{f}", prefer="return=minimal") is None:
                raise RuntimeError(f"{t} : suppression impossible")
        for v in vendus:
            num = next((str(x["id_set"] or "").split("-")[0].removesuffix(".0") for x in arts if x["id"] == v["article_id"]), "")
            base.modifier("ventes", f"id=eq.{v['id']}", {"article_id": article_de({"set": num}) if num else None})
        # Le deal Dealabs redevient « non acheté »
        for d in re.findall(r"dealabs\.com/\S*?-(\d{6,})\b", str(achat[0].get("notes") or "")):
            base.modifier("deals", f"id=eq.{d}", {"achete": False})
        # Les mails de colis qui pointaient sur cette commande n'y sont plus rattachés
        import mails
        tout = mails.tout()
        if any(c.get("achat_id") == ident for c in tout):
            for c in tout:
                if c.get("achat_id") == ident:
                    c.pop("achat_id")
            base.json_partage_ecrire("commandes", mails.FICHIER, tout)
    elif a["table"] == "ventes":
        v = base.lire("ventes", f"select=*&id=eq.{ident}")
        if not v:
            return
        v = v[0]
        mouv = base.lire("tresorerie", f"select=id&vente_id=eq.{ident}")
        if not mouv and str(v.get("date_vente") or "") >= DEBUT_TRESO:
            # Ventes saisies avant le lien vente_id : le mouvement du même jour, même libellé, même montant
            net = round(float(v.get("prix_vente") or 0) * (1 - PRELEVEMENTS), 2)
            mouv = [m for m in base.lire("tresorerie", f"select=id,libelle,montant&le=eq.{v['date_vente']}&type=eq.vente")
                    if abs(float(m["montant"]) - net) < 0.02 and str(m["libelle"]).startswith("Vente : ")][:1]
            if not mouv:  # vendu dans un lot : on retire seulement sa part
                caisse(v["date_vente"], -net, f"Annulation vente {ident} : {v.get('nom_article') or ''}")
        for m in mouv:
            base._appel("DELETE", f"tresorerie?id=eq.{m['id']}", prefer="return=minimal")
        if base._appel("DELETE", f"ventes?id=eq.{ident}", prefer="return=minimal") is None:
            raise RuntimeError("vente : suppression impossible")
    else:
        raise ValueError("table inconnue")


SCANS = Path(__file__).resolve().parent.parent / "stock" / "scans.json"


def noter_scan(a):
    """Scans de l'onglet Scan à garder pour plus tard (fichier privé) : sets trouvés à
    l'inventaire hors de la liste de stock, colis expédiés à recouper avec les mails de vente."""
    tout = base.json_partage("scans", SCANS, [])
    if any(x.get("id") == a.get("id") for x in tout):
        return
    tout.append({"id": a.get("id"), "type": a["type"], "le": a.get("le"), "sets": {str(k): int(v) for k, v in (a.get("sets") or {}).items()}})
    base.json_partage_ecrire("scans", SCANS, tout)


def urssaf_paye(a):
    """Déclaration URSSAF du trimestre payée : la carte de la to-do disparaît."""
    payes = base.json_partage("urssaf", URSSAF, {})
    payes[str(a["echeance"])[:10]] = str(a.get("le") or dt.date.today())[:10]
    base.json_partage_ecrire("urssaf", URSSAF, payes)


URSSAF = Path(__file__).resolve().parent.parent / "stock" / "urssaf.json"
GESTES = {"perte": perte, "stock_trouve": noter_scan, "expedition": noter_scan, "colis_lien": colis_lien, "recu": recu, "cashback_recu": cashback_recu, "cashback_confirme": cashback_confirme, "achete": achete, "achat_libre": achat_libre, "vente": vente, "vente_lot": vente_lot, "vire": vire, "corrige": corrige, "supprime": supprime, "figs_lier": figs_lier, "horizon": horizon, "urssaf_paye": urssaf_paye}


def appliquer(liste: list[dict]) -> dict:
    faites, erreurs = [], {}
    # Dans l'ordre où elles ont été faites : le premier article d'un panier crée la commande.
    for a in sorted(liste, key=lambda x: str(x.get("le") or "")):
        if a.get("fait"):
            continue
        try:
            GESTES[a["type"]](a)
            faites.append(a["id"])
        except Exception as e:
            erreurs[a.get("id")] = str(e)
    return {"faites": faites, "erreurs": erreurs}


def main():
    print(json.dumps(appliquer(json.load(open(sys.argv[1]))), ensure_ascii=False))


if __name__ == "__main__":
    main()
