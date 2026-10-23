"""Passage GitHub : mails de la boîte dédiée, lus par règles fixes (mails_regles).

  - Leboncoin « en ligne » / « supprimée » → lbc_mails.appliquer (coches de l'onglet Platforms) ;
  - suivi de colis (marchand ou transporteur) → noté ; mails.colis le relie à la commande
    par numéro de commande, numéro de suivi ou magasin ;
  - iGraal « cashback en attente » → confirmé sur l'achat de même date et même montant,
    seulement s'il n'y en a qu'un ;
  - pubs sans LEGO et mails sans intérêt → notés comme lus.
Le mail est alors noté comme lu (mails.ajouter) et le passage de Claude ne le relit pas.
Restent pour Claude : confirmations de commande (enregistrement de l'achat), pubs avec
une opération LEGO, annulations, et tout mail que les règles ne lisent pas avec certitude.

Accès en lecture seule (IMAP, BODY.PEEK : le mail reste non lu) avec un mot de
passe d'application de la boîte dédiée, rangé dans les secrets GitHub
GMAIL_ADRESSE et GMAIL_MOT_DE_PASSE. Le journal n'affiche que des nombres.
"""
import email
import email.header
import email.policy
import email.utils
import imaplib
import os
import re
import sys
from zoneinfo import ZoneInfo

import base
import lbc_mails
import mails
import mails_regles


def entete(m, nom: str) -> str:
    return str(email.header.make_header(email.header.decode_header(m.get(nom, ""))))


def corps(m) -> str:
    texte = html = ""
    for p in m.walk():
        t = p.get_content_type()
        if t == "text/plain" and not texte:
            texte = p.get_content()
        elif t == "text/html" and not html:
            html = p.get_content()
    return texte or html


def cashback(e: dict) -> bool:
    """Cashback iGraal « en attente » : l'achat de même date et même montant (à 0,02 € près) passe confirmé."""
    if e["igraal"] != "attente":
        return True
    import appliquer_actions  # seulement quand il y a un cashback à confirmer
    statut = appliquer_actions.colonne_statut()
    achats = base.lire("achats", f"select=id,cashback,notes{',cashback_statut' if statut else ''}&date_achat=eq.{e['d']}")
    c = [a for a in achats if a.get("cashback") is not None and abs(float(a["cashback"]) - e["montant"]) <= 0.02]
    if len(c) != 1:
        return False  # aucun ou plusieurs achats possibles : pour Claude
    a = c[0]
    if (a.get("cashback_statut") or "") in ("en attente", "payé") or "Cashback confirmé" in (a.get("notes") or ""):
        return True
    appliquer_actions.cashback_confirme({"achat_id": a["id"], "le": e["d"]})
    return True


def main():
    adresse, mdp = os.environ.get("GMAIL_ADRESSE"), os.environ.get("GMAIL_MOT_DE_PASSE")
    if not adresse or not mdp:
        print("secrets GMAIL_ADRESSE / GMAIL_MOT_DE_PASSE absents : rien à faire")
        return
    exe = base.debut_execution("mails_leboncoin")
    connus = mails.tout()
    lus = {x.get("mail") for x in connus}
    faits = ignores = 0
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as im:
            im.login(adresse, mdp)
            im.select("INBOX", readonly=True)
            _, r = im.uid("SEARCH", "X-GM-RAW", '"newer_than:3d -from:google.com"')
            for uid in (r[0] or b"").split():
                _, d = im.uid("FETCH", uid, "(X-GM-MSGID BODY.PEEK[])")
                meta = d[0][0].decode()
                mid = format(int(re.search(r"X-GM-MSGID (\d+)", meta).group(1)), "x")
                if mid in lus:
                    continue
                m = email.message_from_bytes(d[0][1], policy=email.policy.default)
                # Seuls les mails authentifiés (DMARC) sont lus, et un mail Leboncoin doit venir de leboncoin.fr
                auth = str(m.get("Authentication-Results", ""))
                if not re.search(r"dmarc=pass", auth):
                    continue
                jour = email.utils.parsedate_to_datetime(m["Date"]).astimezone(ZoneInfo("Europe/Paris")).date().isoformat()
                url = f"https://mail.google.com/mail/?authuser={adresse}#all/{mid}"
                e = mails_regles.lire(mid, entete(m, "From"), entete(m, "Subject"), corps(m), jour, url, connus)
                if not e or e.get("lignes") or (e.get("pub") and e.get("lego")):
                    continue  # pour Claude
                if "lbc" in e:
                    if not re.search(r"dmarc=pass[^;]*header\.from=(?:[\w-]+\.)*leboncoin\.fr", auth):
                        continue
                    if e["lbc"] != "contact":
                        lbc_mails.appliquer(e["set"], e["lbc"], e["titre"], e.get("url", ""))
                elif "igraal" in e and not cashback(e):
                    continue
                mails.ajouter(e)
                connus.append(e)
                if e.get("ignore") or e.get("pub"):
                    ignores += 1
                else:
                    faits += 1
        base.fin_execution(exe, True, f"{faits} mails lus (Leboncoin, colis, cashback), {ignores} ignorés")
        print(faits, "mails lus,", ignores, "ignorés")
    except Exception as err:
        base.fin_execution(exe, False, erreur=type(err).__name__)
        print("erreur :", type(err).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
