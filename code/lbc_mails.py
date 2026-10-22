"""Mails Leboncoin « annonce en ligne » / « annonce supprimée » → onglet Platforms.

Coche toute seule ce que le vendeur a fait sur Leboncoin, sans bouton « Done » :
  - en ligne : l'annonce Vinted du même set est marquée « sur Leboncoin »
    (db leboncoin/<id Vinted>), ou l'action « publier sur Leboncoin » d'un set
    neuf est faite (db plateformes/lbcnew-<set>) ;
  - supprimée : la marque est retirée, et l'action « supprimer sur Leboncoin »
    d'un set déjà vendu ailleurs est faite (db plateformes/rm-lbc-<id>).

Usage : python3 lbc_mails.py <set> en_ligne|supprimee "<titre>" [url]
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

import base

APP = Path(__file__).resolve().parent.parent / "stock" / "app"


def lire(nom, defaut):
    f = APP / nom
    return json.load(open(f)) if f.exists() else defaut


def numero(t: str) -> re.Pattern:
    return re.compile(r"(^|\D)" + re.escape(t) + r"(\D|$)")


def appliquer(s: str, etat: str, titre: str, url: str = ""):
    le = dt.datetime.now(dt.timezone.utc).isoformat()
    on = etat == "en_ligne"
    annonces = [a for a in lire("annonces.json", []) if a.get("n") == s and a.get("s") in ("en ligne", "masqué")]
    lbc = lire("leboncoin.json", {})
    marques = {x["chemin"].split("/", 1)[1]: x.get("data") or {} for x in base.docs("leboncoin/") or []}
    fait = []
    if annonces:
        sur = lambda a: marques[a["id"]].get("on") if a["id"] in marques else bool(lbc.get("lien", {}).get(a["id"]))
        # Une annonce Leboncoin par exemplaire : on coche la première qui n'est pas encore dans cet état
        a = next((a for a in annonces if bool(sur(a)) != on), None)
        if a:
            base.doc_ecrire("leboncoin/" + a["id"], {"on": on, "le": le, **({"url": url} if url else {}), "mail": True})
            fait.append(f"Vinted {a['id']} {'sur' if on else 'retiré de'} Leboncoin")
    elif on:
        base.doc_ecrire("plateformes/lbcnew-" + s, {"etat": "fait", "type": "lbc", "pf": "Leboncoin", "titre": titre[:200], "le": le})
        fait.append("set neuf publié sur Leboncoin")
    if not on:
        n = numero(s)
        for x in lbc.get("seules", []):
            if x.get("vendu") and n.search(x.get("t") or ""):
                base.doc_ecrire("plateformes/rm-lbc-" + x["id"], {"etat": "fait", "type": "retirer", "pf": "Leboncoin", "titre": titre[:200], "le": le})
                fait.append(f"annonce vendue ailleurs {x['id']} supprimée")
    print(s, etat, "→", "; ".join(fait) or "rien à cocher")


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[2] not in ("en_ligne", "supprimee"):
        sys.exit(__doc__)
    appliquer(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
