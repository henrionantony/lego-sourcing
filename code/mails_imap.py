"""Passage GitHub : mails Leboncoin de la boîte dédiée, lus par règles fixes.

« Votre annonce est en ligne » / « … a été supprimée » → lbc_mails.appliquer
(coches de l'onglet Platforms), puis le mail est noté comme lu (mails.ajouter),
ce qui évite que le passage de Claude le relise. Un mail que les règles ne savent
pas lire est laissé tel quel pour Claude.

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


def main():
    adresse, mdp = os.environ.get("GMAIL_ADRESSE"), os.environ.get("GMAIL_MOT_DE_PASSE")
    if not adresse or not mdp:
        print("secrets GMAIL_ADRESSE / GMAIL_MOT_DE_PASSE absents : rien à faire")
        return
    exe = base.debut_execution("mails_leboncoin")
    lus = {x.get("mail") for x in mails.tout()}
    faits = ignores = 0
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as im:
            im.login(adresse, mdp)
            im.select("INBOX", readonly=True)
            _, r = im.uid("SEARCH", "X-GM-RAW", '"from:leboncoin.fr newer_than:3d"')
            for uid in (r[0] or b"").split():
                _, d = im.uid("FETCH", uid, "(X-GM-MSGID BODY.PEEK[])")
                meta = d[0][0].decode()
                mid = format(int(re.search(r"X-GM-MSGID (\d+)", meta).group(1)), "x")
                if mid in lus:
                    continue
                m = email.message_from_bytes(d[0][1], policy=email.policy.default)
                # Seuls les mails authentifiés de leboncoin.fr (DMARC) sont appliqués
                if not re.search(r"dmarc=pass[^;]*header\.from=(?:[\w-]+\.)*leboncoin\.fr", str(m.get("Authentication-Results", ""))):
                    continue
                jour = email.utils.parsedate_to_datetime(m["Date"]).date().isoformat()
                e = mails_regles.lire(mid, entete(m, "From"), entete(m, "Subject"), corps(m), jour, "", [])
                if not e or "lbc" not in e:
                    if e and e.get("ignore"):
                        mails.ajouter(e)
                        ignores += 1
                    continue
                lbc_mails.appliquer(e["set"], e["lbc"], e["titre"], e.get("url", ""))
                mails.ajouter(e)
                faits += 1
        base.fin_execution(exe, True, f"{faits} mails Leboncoin appliqués, {ignores} ignorés")
        print(faits, "mails Leboncoin appliqués,", ignores, "ignorés")
    except Exception as err:
        base.fin_execution(exe, False, erreur=type(err).__name__)
        print("erreur :", type(err).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
