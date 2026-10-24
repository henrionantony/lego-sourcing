"""Contrôle des achats saisis avec les mails de confirmation de commande.

Les mails arrivent dans la boîte dédiée (transfert filtré). La session horaire
les lit et note l'essentiel de chaque commande dans ../stock/mails/commandes.json
(privé) : date, magasin, numéro, lignes {set, q, pu} et total. Jamais de nom,
d'adresse ni de téléphone.

Ce module rapproche chaque commande d'un achat de la base (même magasin, date
proche, au moins un set commun) et compare quantités et montants.
"""
import datetime as dt
import json
import re
from pathlib import Path

import base

FICHIER = Path(__file__).resolve().parent.parent / "stock" / "mails" / "commandes.json"
TOLERANCE = 0.05   # € d'écart toléré (arrondis de la cagnotte)


def _cle(lieu) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(lieu).upper())


def _set(id_set) -> str:
    return str(id_set or "").split("-")[0].removesuffix(".0")


def commandes() -> list[dict]:
    """Commandes d'achat seulement (les mails ignorés et les ventes sont aussi notés dans le fichier)."""
    return [c for c in tout() if c.get("lignes")]


def tout() -> list[dict]:
    """Toutes les entrées (commandes, colis, pubs, mails ignorés), partagées via la base."""
    return base.json_partage("commandes", FICHIER, [])


def ajouter(entree: dict):
    """Ajoute l'entrée d'un mail lu (une seule par id de mail)."""
    t = tout()
    if any(x.get("mail") == entree.get("mail") for x in t):
        return False
    t.append(entree)
    base.json_partage_ecrire("commandes", FICHIER, t)
    return True


def controle() -> dict:
    """{"achats": {achat_id: {"ok", "msg", "num"}}, "sans_achat": [commandes du mail absentes de la base]}"""
    cmds = commandes()
    if not cmds:
        return {"achats": {}, "sans_achat": [], "nums": {}}
    debut = min(c["d"] for c in cmds)
    debut = (dt.date.fromisoformat(debut) - dt.timedelta(days=3)).isoformat()
    achats = base.lire("achats", f"select=id,date_achat,lieu&date_achat=gte.{debut}&limit=1000")
    ids = ",".join(str(a["id"]) for a in achats) or "0"
    arts = base.lire("articles", f"select=achat_id,id_set,quantite,prix_achat,cagnotte&achat_id=in.({ids})&limit=10000")
    par: dict[int, list] = {}
    for x in arts:
        par.setdefault(x["achat_id"], []).append(x)
    res, sans, pris = {}, [], set()
    for c in cmds:
        d = dt.date.fromisoformat(c["d"])
        sets_mail = {_set(l["set"]) for l in c["lignes"]}
        cands = []
        for a in achats:
            if a["id"] in pris or _cle(a["lieu"]) != _cle(c["magasin"]):
                continue
            ecart_j = (dt.date.fromisoformat(a["date_achat"]) - d).days
            lignes = par.get(a["id"], [])
            if -1 <= ecart_j <= 3 and sets_mail & {_set(x["id_set"]) for x in lignes}:
                total = sum(float(x["prix_achat"] or 0) + float(x["cagnotte"] or 0) for x in lignes)
                cands.append((abs(total - c["total"]), a["id"], lignes, total))
        if not cands:
            sans.append(c)
            continue
        _, aid, lignes, total = min(cands)
        pris.add(aid)
        q_base: dict[str, int] = {}
        for x in lignes:
            q_base[_set(x["id_set"])] = q_base.get(_set(x["id_set"]), 0) + int(float(x["quantite"] or 1))
        q_mail: dict[str, int] = {}
        for l in c["lignes"]:
            q_mail[_set(l["set"])] = q_mail.get(_set(l["set"]), 0) + int(l["q"])
        pb = [f"{s}: {q_base.get(s, 0)} logged, {q_mail.get(s, 0)} on the email"
              for s in sorted(set(q_base) | set(q_mail)) if q_base.get(s, 0) != q_mail.get(s, 0)]
        if abs(total - c["total"]) > TOLERANCE:
            pb.append(f"total {total:.2f} € logged, {c['total']:.2f} € on the email")
        res[aid] = {"ok": not pb, "msg": "; ".join(pb) or f"Matches the email ({c['total']:.2f} €)", "num": c.get("num")}
    return {"achats": res, "sans_achat": sans, "nums": {r["num"]: aid for aid, r in res.items() if r.get("num")}}


ORDRE_COLIS = {"expedie": 1, "a_retirer": 2, "livre": 3}


def colis(ouverts: dict[int, dict]) -> dict[int, dict]:
    """Dernier état connu du colis de chaque commande en cours de livraison.

    Les mails de suivi sont notés {"mail", "colis": expedie|a_retirer|livre, "d",
    "num" ou "achat_id" ou "magasin", "lieu" (point de retrait), "jusqu" (date limite)}.
    `ouverts` : {achat_id: {"lieu": magasin, "d": date d'achat}} des commandes pas encore reçues.
    La clé 0 liste les colis à retirer qu'aucun numéro de commande ne relie à une commande.
    """
    entrees = tout()
    nums = controle()["nums"]
    res: dict[int, dict] = {}
    orphelins: list[dict] = []
    a_purger: list[str] = []
    # Numéro de suivi déjà relié à une commande par un autre mail (ex. « expédié » du
    # marchand, puis « arrivé en relais » du transporteur sans numéro de commande)
    par_suivi = {c["suivi"]: c.get("achat_id") or nums.get(c.get("num")) for c in entrees
                 if c.get("suivi") and (c.get("achat_id") or nums.get(c.get("num")))}
    for c in sorted((c for c in entrees if c.get("colis") and not c.get("ecarte")), key=lambda c: (c.get("d") or "", ORDRE_COLIS.get(c["colis"], 0))):
        aid = c.get("achat_id") or nums.get(c.get("num")) or par_suivi.get(c.get("suivi"))
        if not aid and c.get("magasin"):
            # sans numéro : seulement s'il n'y a qu'une commande ouverte dans ce magasin,
            # sinon on ne devine pas (le colis est signalé à part)
            cands = [a for a, o in ouverts.items() if _cle(o["lieu"]) == _cle(c["magasin"])]
            aid = cands[0] if len(cands) == 1 else None
        if aid not in ouverts:
            if aid and (c.get("pass") or c.get("code")):
                a_purger.append(c["mail"])   # commande reçue : le code de retrait ne sert plus
            if c["colis"] == "a_retirer" and not aid:
                orphelins.append({**c, "candidats": [a for a, o in ouverts.items() if not c.get("magasin")
                                                     or _cle(o["lieu"]) == _cle(c["magasin"])] or list(ouverts)})
            continue
        # Plusieurs mails pour un même colis (marchand + transporteur) : on garde
        # l'étape la plus avancée, sans jamais revenir en arrière, et on complète
        # le lieu et la date limite avec ce que donne chaque mail.
        av = res.get(aid, {})
        if ORDRE_COLIS.get(c["colis"], 0) >= ORDRE_COLIS.get(av.get("colis"), 0):
            av = {**av, "colis": c["colis"], "d": c.get("d")}
        for k in ("lieu", "jusqu", "suivi", "transporteur", "pass", "code"):
            # le dernier mail « à retirer » fait foi pour le relais (colis redirigé)
            if c.get(k) and (not av.get(k) or (c["colis"] == "a_retirer" and k in ("lieu", "jusqu", "pass", "code"))):
                av[k] = c[k]
        res[aid] = av
    # Codes de retrait vieux de 3 semaines (colis jamais relié) : purgés aussi
    vieux = (dt.date.today() - dt.timedelta(days=21)).isoformat()
    a_purger += [c["mail"] for c in entrees if (c.get("pass") or c.get("code")) and (c.get("d") or "") < vieux]
    if a_purger:
        purger_retraits(set(a_purger))
    if orphelins:
        res[0] = {"orphelins": orphelins}   # colis prêts qu'on n'a pas su rattacher à une commande
    return res


def purger_retraits(ids: set[str]):
    """Efface QR et code de retrait des mails donnés (colis retiré ou trop vieux)."""
    t = tout()
    for x in t:
        if x.get("mail") in ids:
            x.pop("pass", None)
            x.pop("code", None)
    base.json_partage_ecrire("commandes", FICHIER, t)


if __name__ == "__main__":
    # python3 mails.py ajouter '<entrée JSON>' : note un mail lu (passage horaire)
    import sys
    if sys.argv[1:2] == ["ids"]:
        print(" ".join(str(x.get("mail")) for x in tout()))
    elif sys.argv[1:2] == ["retrait"]:
        # python3 mails.py retrait <fichier> : QR et code de retrait d'un mail Pickup
        # (fichier = résultat JSON de get_message FULL_CONTENT, ou HTML brut), à ajouter à l'entrée
        import mails_regles
        brut = Path(sys.argv[2]).read_text()
        try:
            brut = json.loads(brut).get("htmlBody") or brut
        except ValueError:
            pass
        print(json.dumps(mails_regles.retrait(brut, mails_regles.texte(brut))))
    elif sys.argv[1:2] == ["ajouter"]:
        print("ajouté" if ajouter(json.loads(sys.argv[2])) else "déjà noté")
    else:
        print(json.dumps(controle(), ensure_ascii=False, indent=1))
