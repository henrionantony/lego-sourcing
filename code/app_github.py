"""Passage de l'application LEGO HQ, lancé par GitHub toutes les 30 minutes (aucune
session Claude) : applique les gestes faits dans l'application, recopie les comptages
d'inventaire, puis recalcule l'état affiché. Tout passe par la table app_docs.

Usage : python3 app_github.py
"""
import json
import tempfile
from pathlib import Path

import appliquer_actions
import base
import etat_app
import inventaire


def gestes() -> str:
    docs = base.docs("actions/")
    if docs is None:
        raise RuntimeError("lecture des gestes impossible")
    a_faire = [{**d["data"], "id": d["chemin"].removeprefix("actions/")} for d in docs
               if not d["data"].get("fait")]
    if not a_faire:
        return "0 geste"
    r = appliquer_actions.appliquer(a_faire)
    for a in a_faire:
        if a["id"] in r["faites"]:
            base.doc_ecrire("actions/" + a["id"], {**{k: v for k, v in a.items() if k != "id"}, "fait": True})
        elif a["id"] in r["erreurs"]:
            base.doc_ecrire("actions/" + a["id"], {**{k: v for k, v in a.items() if k != "id"},
                                                   "erreur": r["erreurs"][a["id"]][:300]})
    return f"{len(r['faites'])} geste(s) appliqué(s), {len(r['erreurs'])} en erreur"


def comptes():
    docs = base.docs("comptes/") or []
    with tempfile.TemporaryDirectory() as t:
        (Path(t) / "comptes").mkdir()
        for d in docs:
            (Path(t) / "comptes" / (d["chemin"].removeprefix("comptes/") + ".json")).write_text(json.dumps(d["data"]))
        inventaire.main(t)


def main():
    if base.docs("etat/") is None:
        print("table app_docs absente ou base injoignable : rien à faire")
        return
    ide = base.debut_execution("application")
    try:
        resume = gestes()
        comptes()
        etat_app.main()
        for nom in ("principal", "historique"):
            f = etat_app.SORTIE if nom == "principal" else etat_app.SORTIE.parent / "historique.json"
            if not base.doc_ecrire("etat/" + nom, json.loads(f.read_text())):
                raise RuntimeError(f"écriture de etat/{nom} refusée")
        base.fin_execution(ide, True, resume)
    except BaseException as e:
        base.fin_execution(ide, False, erreur=repr(e))
        raise


if __name__ == "__main__":
    main()
