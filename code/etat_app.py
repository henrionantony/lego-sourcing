"""État de l'application centrale : un seul fichier JSON que la session horaire
copie dans la base de l'application (document « etat/principal »).

Contient : santé des automatisations, trésorerie, objectifs de l'année,
choses à régulariser et derniers deals. Écrit dans ../stock/app/etat.json (privé).

Usage : python3 etat_app.py
"""
import datetime as dt
import json
import re
import os
import ssl
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import base
import mails
from regles import PRELEVEMENTS

ICI = Path(__file__).resolve().parent
STOCK = ICI.parent / "stock"
SORTIE = STOCK / "app" / "etat.json"
RAPPELS = STOCK / "app" / "rappels.json"   # rappels écrits à la main (privé)
DEPOT = "henrionantony/lego-sourcing"
DEBUT_ANNEE = dt.date(2026, 10, 1)          # année 4 de l'activité
# Objectifs de l'année : dans le fichier privé parametres.json, jamais dans le code
_OBJ = json.loads((ICI.parent / "data" / "parametres.json").read_text()).get("OBJECTIFS_ANNEE", {})
CIBLE_CA, CIBLE_NET = _OBJ.get("ca", 0), _OBJ.get("net", 0)


def partage(nom: str, calcul):
    """Donnée tirée d'un fichier privé : calculée là où le fichier existe (session cloud)
    et rangée dans la base ; ailleurs (GitHub) relue dans la base."""
    try:
        v = calcul()
    except FileNotFoundError:
        v = None
    if v is not None:
        base.doc_ecrire("fichiers/" + nom, {"v": v})
        return v
    return (base.doc("fichiers/" + nom) or {}).get("v")


def _lundi():
    """Dernière liste d'actions du lundi : date, annonces masquées à réafficher, baisses de prix."""
    import pandas as pd
    fichiers = sorted((STOCK / "actions").glob("actions_semaine_*.xlsx"))
    if not fichiers:
        return None
    out = {"date": fichiers[-1].stem.split("_")[-1], "reafficher": None, "baisses": {}}
    try:
        out["reafficher"] = len(pd.read_excel(fichiers[-1], sheet_name="Réafficher"))
    except Exception as e:
        print(f"liste du lundi illisible : {e}")
    # Une liste sans baisse (celles de la semaine d'avant sont encore dans leur
    # délai de 30 jours) ne doit pas masquer la dernière liste qui en propose.
    for f in reversed(fichiers):
        try:
            df = pd.read_excel(f, sheet_name="Baisser de 5 %")
        except Exception as e:
            print(f"baisses illisibles ({f.name}) : {e}")
            continue
        if len(df):
            prix = {}
            for _, r in df.iterrows():
                m = re.search(r"/items/(\d+)", str(r["Lien"]))
                if m:
                    prix[m.group(1)] = float(r["Nouveau prix"])
            out["baisses"] = {"liste": f.stem[-10:], "prix": prix}
            break
    return out


_LUNDI = None


def lundi() -> dict:
    global _LUNDI
    if _LUNDI is None:
        _LUNDI = partage("lundi", _lundi) or {}
    return _LUNDI


def maintenant():
    return dt.datetime.now(dt.timezone.utc)


def github_runs() -> list[dict]:
    """Derniers passages du workflow d'alertes (API publique de GitHub)."""
    ctx = ssl.create_default_context(cafile="/root/.ccr/ca-bundle.crt") if os.path.exists("/root/.ccr/ca-bundle.crt") else None
    url = f"https://api.github.com/repos/{DEPOT}/actions/workflows/alertes.yml/runs?per_page=10"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "lego-app"})
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return json.load(r).get("workflow_runs", [])
    except Exception as e:
        print(f"github : {e}")
        return []


def automatisations() -> list[dict]:
    out = []
    runs = github_runs()
    if runs:
        # Le passage en cours n'est pas forcément le plus récent : un passage horaire attend son tour (« pending ») derrière lui.
        r = next((x for x in runs if x["status"] == "in_progress"), runs[0])
        fini = [x for x in runs if x["status"] == "completed"]
        echecs = [x for x in fini[:5] if x["conclusion"] not in ("success", "cancelled")]
        if r["status"] in ("in_progress", "queued", "pending", "waiting"):
            debut = dt.datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Paris"))
            etat, detail = "ok", f"Running since {debut:%H:%M}: checks Dealabs every 30 seconds"
        else:
            etat, detail = "attention", "Not running right now: the hourly safety net will restart it"
        if echecs:
            etat, detail = "erreur", f"{len(echecs)} of the last {min(len(fini), 5)} runs failed"
        # Le job GitHub reste « vert » même quand le script plante à chaque passage (21/10) :
        # on regarde aussi les derniers passages notés dans la base.
        der = base.lire("executions", "select=ok,erreur&tache=eq.alertes&fin=not.is.null&order=id.desc&limit=3")
        if len(der) == 3 and not any(x["ok"] for x in der):
            etat, detail = "erreur", "BROKEN: last 3 checks crashed, no Telegram alert is sent (" + (der[0]["erreur"] or "")[:120] + ")"
        quand = maintenant().isoformat() if r["status"] in ("in_progress", "queued", "pending", "waiting") else r["updated_at"]
        out.append({"nom": "Deal alerts (GitHub)", "etat": etat, "detail": detail, "quand": quand,
                    "lien": r["html_url"]})
    else:
        out.append({"nom": "Deal alerts (GitHub)", "etat": "attention", "detail": "Status unavailable", "quand": None})
    ex = base.lire("executions", "select=tache,debut,fin,ok,resume,erreur&order=debut.desc&limit=50")
    noms = {"application": "App refresh (GitHub)", "alertes": "Latest Dealabs check", "actions_semaine": "Monday action list", "collecte": "Daily data refresh"}
    for tache, nom in noms.items():
        # Le rafraîchissement de l'appli tourne pendant ce calcul : on montre son dernier passage terminé
        e = next((x for x in ex if x["tache"] == tache and (x["fin"] or tache != "application")), None)
        if not e:
            continue
        etat = "ok" if e["ok"] else ("erreur" if e["fin"] else "attention")
        detail = e["resume"] or e["erreur"] or ""
        for fr, en in [("nouveaux deals", "new deals"), ("achète", "buy"), ("à regarder", "take a look"), (" non", " skip")]:
            detail = detail.replace(fr, en)
        detail = detail.replace("0 geste", "no button tapped").replace(" geste(s) appliqué(s)", " button action(s) applied").replace("en erreur", "failed")
        if tache == "application":
            detail = "Every 5 min, and right away on a new deal or a button tap" + (" · " + detail if detail else "")
        out.append({"nom": nom, "etat": etat, "detail": detail.rstrip(" :"), "quand": e["fin"] or e["debut"]})
    if "actions_semaine" not in {x["tache"] for x in ex}:
        d = lundi().get("date")
        if d:
            out.append({"nom": "Monday action list", "etat": "ok", "detail": f"Last list {jour(d)}", "quand": d})
    ref = ICI.parent / "data" / "referentiel.csv"
    if ref.exists() and "collecte" not in {x["tache"] for x in ex}:
        quand = dt.datetime.fromtimestamp(ref.stat().st_mtime, dt.timezone.utc)
        # Sur GitHub, la table est déchiffrée à chaque passage : sa vraie date est notée à côté
        maj = ICI.parent / "data" / "referentiel_maj.txt"
        if maj.exists():
            quand = dt.datetime.fromisoformat(maj.read_text().strip())
        age = (maintenant() - quand).total_seconds() / 3600
        out.append({"nom": "Daily data refresh", "etat": "ok" if age < 30 else "erreur",
                    "detail": "Prices, retirements and demand updated" if age < 30 else f"Not updated for {age:.0f} h",
                    "quand": quand.isoformat()})
    return out


def objectifs() -> dict:
    annee = base.lire("ventes", f"select=date_vente,prix_vente&date_vente=gte.{DEBUT_ANNEE}&limit=10000")
    ca = sum(float(v["prix_vente"] or 0) for v in annee)
    d = base.lire("ventes", "select=date_vente&date_vente=not.is.null&order=date_vente.desc&limit=1")
    derniere = d[0]["date_vente"] if d else None
    mois = {}
    for v in annee:
        mois[v["date_vente"][:7]] = mois.get(v["date_vente"][:7], 0) + float(v["prix_vente"] or 0)
    return {"debut": DEBUT_ANNEE.isoformat(), "ca": round(ca, 2), "cible_ca": CIBLE_CA, "cible_net": CIBLE_NET,
            "net_frais": round(ca * (1 - PRELEVEMENTS), 2), "ventes": len(annee), "derniere_vente": derniere,
            "mois": mois}


def jour(iso) -> str:
    return dt.date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y") if iso else "?"


def magasin(lieu) -> str:
    return {"LEGO.COM": "LEGO.com"}.get(str(lieu), str(lieu).title())


_NOMS: dict | None = None


def _compter(lignes: list) -> dict:
    """Quantité attendue par numéro de set (sans suffixe)."""
    n: dict = {}
    for x in lignes:
        s = str(x.get("id_set") or "").split("-")[0].removesuffix(".0")
        if s:
            n[s] = n.get(s, 0) + int(float(x["quantite"] or 1))
    return n


def nom_set(article: dict) -> str:
    """Nom anglais du set (référentiel) suivi du numéro, sinon la désignation de la base."""
    global _NOMS
    if _NOMS is None:
        import csv
        with open(ICI.parent / "data" / "referentiel.csv", newline="") as f:
            _NOMS = {r["base"]: r["name"] for r in csv.DictReader(f) if r.get("name")}
    s = str(article.get("id_set") or "").split("-")[0].removesuffix(".0")
    if s in _NOMS:
        return f"{_NOMS[s]} · {s}"
    return str(article["designation"]).removeprefix("LEGO ").capitalize()


def eur(x) -> str:
    return f"{x:.2f} €".replace(".", ",")


def virements_dus() -> dict[int, list]:
    """Cagnottes pas encore reversées sur le compte LEGO, par achat (achats saisis depuis l'application)."""
    cag = base.lire("articles", "select=achat_id,cagnotte,designation,id_set&cagnotte=gt.0&cree_le=gte.2026-10-19&limit=1000")
    if not cag:
        return {}
    faits = {m["achat_id"] for m in base.lire("tresorerie", "select=achat_id&libelle=like.Cagnotte*&limit=10000")}
    # Achats de rattrapage (datés d'avant la trésorerie de départ) : rien à reverser
    ids = ",".join(str(i) for i in {x["achat_id"] for x in cag})
    # sauf ceux marqués « cagnotte à reverser » (pas encore remboursée à l'époque)
    vieux = {r["id"] for r in base.lire("achats", f"select=id,notes&id=in.({ids})&date_achat=lt.2026-10-18&limit=1000")
             if "[cagnotte à reverser]" not in str(r.get("notes") or "")}
    faits |= vieux
    dus: dict[int, list] = {}
    for x in cag:
        if x["achat_id"] not in faits:
            dus.setdefault(x["achat_id"], []).append(x)
    return dus


def a_regulariser(obj: dict) -> list[dict]:
    out = []
    # Commandes en cours de livraison : à confirmer à la réception
    arts = base.lire("articles", "select=id,achat_id,id_set,designation,quantite,notes&notes=ilike.*livraison*&limit=1000")
    achats = {a["id"]: a for a in base.lire("achats", "select=*&cashback=gt.0&limit=10000")}
    for aid in {a["achat_id"] for a in arts} - set(achats):
        achats.update({x["id"]: x for x in base.lire("achats", f"select=*&id=eq.{aid}")})
    par_achat: dict[int, list] = {}
    for a in arts:
        par_achat.setdefault(a["achat_id"], []).append(a)
    # Une seule carte pour toutes les livraisons, les plus anciennes d'abord
    livs = []
    suivi = mails.colis({aid: {"lieu": achats.get(aid, {}).get("lieu"), "d": achats.get(aid, {}).get("date_achat")} for aid in par_achat})
    orph = suivi.pop(0, {}).get("orphelins", [])
    for aid, lignes in sorted(par_achat.items(), key=lambda kv: (kv[0] not in suivi or suivi[kv[0]]["colis"] != "a_retirer",
                                                                 str(achats.get(kv[0], {}).get("date_achat")), kv[0])):
        a = achats.get(aid, {})
        n = sum(int(float(x["quantite"] or 1)) for x in lignes)
        sv = suivi.get(aid)
        etat = ""
        if sv:
            # l'étape elle-même est montrée par la ligne de points ; ici seulement où et jusqu'à quand
            etat = (f" · at {sv['lieu']}" if sv.get("lieu") and sv["colis"] == "a_retirer" else "") \
                   + (f" until {jour(sv['jusqu'])}" if sv.get("jusqu") else "")
        # Ligne de points dans l'application : 0 commandée, 1 expédiée, 2 à retirer / livrée
        etape = mails.ORDRE_COLIS.get(sv["colis"], 0) if sv else 0
        suivi_url = None
        if sv and sv.get("suivi"):
            suivi_url = (f"https://www.chronopost.fr/tracking-no-cms/suivi-page?listeNumerosLT={sv['suivi']}"
                         if "chrono" in (sv.get("transporteur") or "").lower()
                         else f"https://www.laposte.fr/outils/suivre-vos-envois?code={sv['suivi']}")
        livs.append({"id": f"recu-{aid}", "d": a.get("date_achat"), "titre": f"{magasin(a.get('lieu'))} · {jour(a.get('date_achat'))}",
                     "sous": f"{n} item{'s' if n > 1 else ''}{etat}", "pret": bool(sv and sv["colis"] == "a_retirer"),
                     "etape": min(etape, 2), "arrivee": "Pick up" if sv and sv["colis"] == "a_retirer" else "Delivered" if etape == 3 else "Arrived",
                     "suivi": sv.get("suivi") if sv else None, "suivi_url": suivi_url,
                     "articles": [f"{int(float(x['quantite'] or 1))} × {nom_set(x)}" for x in lignes],
                     # Pour le scan à la réception : quantités attendues par numéro de set
                     "sets": {k: v for k, v in _compter(lignes).items()},
                     "action": {"type": "recu", "achat_id": aid, "label": "Received"}})
    # Colis prêts qu'aucun numéro ne relie à une commande : carte rouge à part, en tête
    if orph:
        lignes = []
        for c in orph:
            info = [x for x in (c.get("transporteur"), c.get("suivi") and f"tracking {c['suivi']}",
                                c.get("jusqu") and f"pick up before {jour(c['jusqu'])}", c.get("sujet")) if x]
            lignes.append({"id": f"colis-{c['mail']}", "d": c.get("d"),
                           "titre": f"{c.get('lieu') or 'Pickup point ?'} · email of {jour(c.get('d'))}",
                           "sous": " · ".join(info), "url": c.get("url"),
                           "choix": [{"label": f"{magasin(achats.get(a, {}).get('lieu'))} {jour(achats.get(a, {}).get('date_achat'))[:5]} · "
                                               + ", ".join(nom_set(x).split(" · ")[-1] for x in par_achat.get(a, [])),
                                      "action": {"type": "colis_lien", "mail": c["mail"], "achat_id": a}} for a in c["candidats"]]
                                    + [{"label": "Not mine / ignore", "action": {"type": "colis_lien", "mail": c["mail"], "achat_id": 0}}]})
        out.append({"id": "colis-inconnus", "type": "ecart", "titre": f"📦 {len(lignes)} parcel{'s' if len(lignes) > 1 else ''} to pick up, order unknown",
                    "detail": "A carrier says a parcel is waiting but doesn't give the order number. Open the email, then tap the order it belongs to.",
                    "lignes": lignes})
    if livs:
        prets = sum(l["pret"] for l in livs)
        out.append({"id": "livraisons", "type": "livraison", "titre": f"{len(livs)} order{'s' if len(livs) > 1 else ''} on the way"
                    + (f" · {prets} ready to pick up" if prets else ""),
                    "detail": "Ready to pick up first, then oldest first. Open an order to check each quantity, then tap Received when everything is there.",
                    "lignes": livs})
    # Cashback : à confirmer (rien reçu d'iGraal) → en attente (iGraal l'a enregistré) → payé.
    # Une seule carte pour tous, sinon la liste s'allonge pendant les 6 à 12 mois d'attente.
    cb = []
    for a in achats.values():
        if a.get("cashback") and not a.get("cashback_recu"):
            statut = a.get("cashback_statut") or ("en attente" if "cashback confirmé" in str(a.get("notes") or "") else "à confirmer")
            cb.append({"achat_id": a["id"], "lieu": magasin(a["lieu"]), "date": jour(a["date_achat"]), "montant": round(float(a["cashback"]), 2),
                       "statut": statut, "mois": (dt.date.today() - dt.date.fromisoformat(a["date_achat"])).days // 30})
    if cb:
        cb.sort(key=lambda c: str(c["date"] or ""), reverse=True)  # du plus récent au plus ancien
        cb.sort(key=lambda c: c["statut"] != "à confirmer")  # tri stable : l'ordre par date reste dans chaque étape
        a_confirmer = sum(c["statut"] == "à confirmer" for c in cb)
        out.append({"id": "cashback", "type": "cashback", "titre": f"Cashback: {sum(c['montant'] for c in cb):.2f} € expected",
                    "detail": (f"{a_confirmer} to confirm once iGraal shows it as pending. " if a_confirmer else "")
                              + "Pending cashback is usually paid 6 to 12 months later.",
                    "cashback": cb,
                    # dans la liste des choses à faire seulement s'il y a un geste à faire
                    "calme": not a_confirmer and all(c["mois"] < 6 for c in cb)})
    # Cagnottes à reverser du compte perso vers le compte pro (achats saisis depuis l'application)
    vir = []
    for aid, lignes in virements_dus().items():
        m = round(sum(float(x["cagnotte"]) for x in lignes), 2)
        a = achats.get(aid) or (base.lire("achats", f"select=*&id=eq.{aid}") or [{}])[0]
        vir.append({"id": f"vire-{aid}", "d": a.get("date_achat"), "titre": f"{eur(m)} · {magasin(a.get('lieu'))} · {jour(a.get('date_achat'))}",
                    "sous": "loyalty credit", "articles": [nom_set(x) for x in lignes],
                    "action": {"type": "vire", "achat_id": aid, "montant": m, "label": "Transferred"}})
    if vir:
        vir.sort(key=lambda v: (str(v["d"]), v["id"]), reverse=True)  # le plus récent en haut
        tot = sum(v["action"]["montant"] for v in vir)
        out.append({"id": "virements", "type": "transfert",
                    "titre": f"Move {eur(tot)} from your personal account to the LEGO account",
                    "detail": "Loyalty credit lands on your personal card while the LEGO account paid the full price. "
                              "Tap Transferred for each order once the money is moved.",
                    "lignes": vir})
    # Achats qui ne collent pas avec le mail de confirmation, commandes reçues par mail mais pas saisies
    ctl = mails.controle()
    pbs = [{"id": f"mail-{aid}", "titre": f"Order #{aid}", "sous": r["msg"]} for aid, r in ctl["achats"].items() if not r["ok"]]
    pbs += [{"id": f"mail-{c.get('num') or c['mail']}", "titre": f"{magasin(c['magasin'])} · {jour(c['d'])} · {eur(c['total'])}",
             "sous": "On an email but not logged: " + ", ".join(f"{l['q']} × {l['set']}" for l in c["lignes"])}
            for c in ctl["sans_achat"]]
    if pbs:
        out.append({"id": "controle", "type": "ecart", "titre": f"{len(pbs)} purchase{'s' if len(pbs) > 1 else ''} to fix",
                    "detail": "Your log and the confirmation email disagree. Fix the line in History (Edit), "
                              "or log the missing order with Log a purchase.", "lignes": pbs})
    # Ventes non saisies
    if obj["derniere_vente"] and obj["derniere_vente"] < (dt.date.today() - dt.timedelta(days=14)).isoformat():
        out.append({"id": "ventes", "type": "ventes", "titre": f"Sales not logged since {jour(obj['derniere_vente'])}",
                    "detail": "Your goals and stock stay wrong until these sales are in. The Vinted export lists the sold items; Leboncoin and in-person sales need to be added by hand."})
    # Annonces masquées à réafficher (liste du lundi)
    n = lundi().get("reafficher")
    if n:
        out.append({"id": "masquees", "type": "annonces", "titre": f"{n} hidden Vinted listings",
                    "detail": "Check you still have them (Stock tab, filter Hidden on Vinted), then make them visible again."})
    # Double vente : set vendu sur Vinted mais annonce encore en ligne sur Leboncoin
    lbc = partage("leboncoin", lambda: json.loads((STOCK / "app" / "leboncoin.json").read_text()))
    if lbc:
        vendus = [x for x in lbc.get("seules", []) if x.get("vendu")]
        if vendus:
            out.append({"id": "lbc-vendus", "type": "annonces", "titre": f"{len(vendus)} Leboncoin ad{'s' if len(vendus) > 1 else ''} for sets sold on Vinted",
                        "detail": "Delete them on Leboncoin unless you still have another copy. Your next Leboncoin export clears this card.",
                        "articles": [x["t"] for x in vendus]})
    # Trésorerie
    if base.tresorerie() is None:
        out.append({"id": "tresorerie", "type": "reglage", "titre": "Set up the cash balance",
                    "detail": "Paste the treasury SQL in Supabase and confirm your starting cash, so alerts know how much you can spend."})
    out += partage("rappels", lambda: json.loads(RAPPELS.read_text())) or []
    return out


def explication_note(nt: dict) -> str:
    rot = round(50 * min(max(nt["an"], 0) / 1.5, 1))
    eur = round(50 * min(max(nt["gain"], 0) / 40, 1))
    return (f"Note {nt['lettre']} {nt['score']}/100 = rotation {rot}/50 (≈ {nt['an']:.0%} par an, plein à 150 %) "
            f"+ gain {eur}/50 ({nt['gain']:.2f} € nets, plein à 40 €), × risque {nt['risque']} × demande {nt.get('demande') or 'inconnue'}")


RISQUE_POIDS = {"sûr": 1.0, "moyen": 0.75, "risqué": 0.45}
DEMANDE_POIDS = {"forte": 1.0, "moyenne": 0.8, "faible": 0.5}


def memo_note(nt: dict) -> list[str]:
    """Comment la note est construite, ligne par ligne, pour arbitrer entre deals."""
    rot = round(50 * min(max(nt["an"], 0) / 1.5, 1))
    eur = round(50 * min(max(nt["gain"], 0) / 40, 1))
    r = nt.get("risque") or "inconnu"
    dm = str(nt.get("demande") or "").split(" ")[0] or "inconnue"
    return [f"Note {nt['score']}/100 → {nt['lettre']} (A dès 60, B dès 35, C en dessous)",
            f"Rotation, moitié de la note : {nt['an']:.0%} net par an → {rot}/50 (plein à 150 %/an)",
            f"Gain par set, l'autre moitié : {nt['gain']:.2f} € net → {eur}/50 (plein à 40 €)",
            f"× Risque {r} : ×{RISQUE_POIDS.get(r, 0.6):g} (sûr ×1 · moyen ×0,75 · risqué ×0,45)",
            f"× Demande {dm} : ×{DEMANDE_POIDS.get(dm, 0.7):g} (forte ×1 · moyenne ×0,8 · faible ×0,5 · inconnue ×0,7)",
            "Net = après les 21 % de prélèvements. Points Insiders et cashback ne sont pas comptés.",
            "Hors note : quantité, tréso et diversification (voir Scénarios)."]


VIGNETTES = Path(__file__).resolve().parent.parent / "data" / "cache" / "vignettes"


def vignette(id_set: str) -> str | None:
    """Petite photo du set (Rebrickable) en data URI, gardée en cache : l'application
    ne peut pas charger d'images externes."""
    import base64, io
    VIGNETTES.mkdir(parents=True, exist_ok=True)
    f = VIGNETTES / f"{id_set}.txt"
    if f.exists():
        return f.read_text() or None
    uri = ""
    try:
        from PIL import Image
        brut = urllib.request.urlopen(f"https://cdn.rebrickable.com/media/sets/{id_set}-1.jpg", timeout=30).read()
        im = Image.open(io.BytesIO(brut)).convert("RGB")
        im.thumbnail((112, 112))
        fond = Image.new("RGB", (112, 112), "white")
        fond.paste(im, ((112 - im.width) // 2, (112 - im.height) // 2))
        tampon = io.BytesIO()
        fond.save(tampon, "JPEG", quality=62)
        uri = "data:image/jpeg;base64," + base64.b64encode(tampon.getvalue()).decode()
    except Exception as e:
        print(f"photo {id_set} indisponible : {e}")
        if getattr(e, "code", None) != 404:
            return None  # erreur passagère : on réessaiera au prochain passage
    f.write_text(uri)
    return uri or None


def deals() -> list[dict]:
    lignes = base.lire("deals", "select=id,vu_le,titre,lien,id_set,prix,verdict,prix_max,raison,deja_en_stock,achete,temperature"
                                "&order=vu_le.desc&limit=60")
    # Verdict, note et détails recalculés avec les règles et la trésorerie du moment,
    # pour montrer dans l'application les mêmes infos que l'alerte Telegram.
    try:
        import alertes
        from referentiel import charger
        from regles import juger_set
        ref, tresor, stock = charger(), base.tresorerie(), base.stock_par_set()
        part_out, engage = base.part_out_par_gamme(), base.engage_par_gamme()
    except Exception as e:
        print(f"détails des deals indisponibles : {e}")
        ref = None
    out = []
    for d in lignes:
        if ref is not None and d.get("id_set") and d.get("prix"):
            v = juger_set(d["id_set"], float(d["prix"]), ref)
            nt = {}
            det = alertes.details(d["id_set"], float(d["prix"]), ref, tresor, stock.get(d["id_set"], 0), part_out, nt, engage)
            d.update(verdict="à regarder" if nt.get("assez") and v.decision == "achète" else v.decision, prix_max=round(v.prix_max, 2) if v.prix_max else None, raison=v.raison, **nt,
                     # Lignes déjà montrées autrement sur la carte : stratégie, prix public, retrait
                     details=[l for l in det if not l.startswith(("👉", "Prix public", "🔚", "✋", "Quantité conseillée"))]
                     , memo=memo_note(nt) if nt.get("lettre") else None, img=vignette(d["id_set"]),
                     diversif=any(l.startswith("⚖") for l in det))
            x = ref[ref.base == str(d["id_set"])]
            if len(x):
                x = x.iloc[0]
                rc = x.get("retrait_croise")
                rr = x.get("retrait")
                rt = str(rc if rc == rc and rc else (rr if rr == rr and rr else ""))[:10]
                if x.get("statut") == "retiré":
                    d["retrait"] = {"etat": "retiré", "date": rt or None}
                elif rt:
                    d["retrait"] = {"etat": "bientôt" if dt.date.fromisoformat(rt) <= dt.date.today() + dt.timedelta(days=183) else "plus tard",
                                    "date": rt, "sur": str(x.get("niveau_retrait") or "") if x.get("niveau_retrait") == x.get("niveau_retrait") else ""}
        out.append({k: v for k, v in d.items() if v is not None})
    return out


def origines() -> list[dict]:
    """Articles en stock d'où peut venir une figurine vendue : sets découpés,
    magazines et comics, lots de figurines, figurines achetées seules."""
    genre = {"COMICS": "Magazine", "LEGO COMICS": "Magazine", "LOT COMICS": "Magazine",
             "MINIFIGS": "Minifig", "LOT MINIFIGURES": "Minifig lot"}
    out = []
    for r in base.lire("stock", "select=id,id_set,designation,gamme,categorie,strategie,date_achat,lieu&reste=gt.0&limit=10000"):
        k = genre.get(r["categorie"]) or ("Part-out set" if "LOT" in str(r["strategie"] or "") else None)
        if k:
            out.append({"id": r["id"], "t": (str(r["designation"] or "").strip() or f"{r['gamme'] or ''} {r['id_set'] or ''}".strip() or "?")[:90], "k": k,
                        "d": str(r["date_achat"] or "")[:7], "l": str(r["lieu"] or "")[:30]})
    return out


def baisses() -> dict:
    """Baisses de prix de la dernière liste du lundi : {id annonce Vinted: nouveau prix}."""
    return lundi().get("baisses") or {}


def historique(n_achats: int = 120, n_ventes: int = 250) -> dict:
    """Derniers achats et dernières ventes, les plus récents d'abord, pour l'onglet History de l'application."""
    achats = base.lire("achats", f"select=id,date_achat,lieu,cashback,points_gagnes,notes"
                                 f"&order=date_achat.desc.nullslast,id.desc&limit={n_achats}")
    ids = ",".join(str(a["id"]) for a in achats) or "0"
    arts = base.lire("articles", f"select=id,achat_id,quantite,id_set,designation,prix_achat,prix_total,offert,cagnotte,"
                                 f"co_investisseur,points_utilises&achat_id=in.({ids})&order=id&limit=10000")
    par = {}
    for x in arts:
        par.setdefault(x["achat_id"], []).append(x)
    ctl, dus = mails.controle()["achats"], virements_dus()
    ha = []
    for a in achats:
        lignes = par.get(a["id"], [])
        prix = lambda x: float(x.get("prix_total") or x.get("prix_achat") or 0)
        ha.append({"id": a["id"], "d": a["date_achat"], "l": magasin(a["lieu"]),
                   "t": round(sum(prix(x) for x in lignes), 2),
                   "c": round(sum(float(x.get("cagnotte") or 0) for x in lignes), 2) or None,
                   "cb": a.get("cashback"), "pt": a.get("points_gagnes"), "no": a.get("notes") or "",
                   "it": [[x.get("quantite") or 1, nom_set(x), round(prix(x), 2), 1 if x.get("offert") else 0,
                           1 if x.get("co_investisseur") else 0, x["id"], float(x.get("cagnotte") or 0) or None,
                           x.get("designation") or "", 0, x.get("points_utilises") or None] for x in lignes]})
        if a["id"] in ctl:
            ha[-1]["x"] = ctl[a["id"]]
        if a["id"] in dus:
            ha[-1]["rg"] = "Loyalty credit not moved to the LEGO account yet"
    ventes = base.lire("ventes", f"select=id,article_id,date_vente,plateforme,produit,nom_article,quantite,prix_vente,"
                                 f"co_investisseur&order=date_vente.desc.nullslast,id.desc&limit={n_ventes}")
    ids = ",".join({str(v["article_id"]) for v in ventes if v.get("article_id")}) or "0"
    sets = {x["id"]: x for x in base.lire("articles", f"select=id,id_set,designation&id=in.({ids})&limit=10000")}
    hv = []
    for v in ventes:
        x = sets.get(v.get("article_id"))
        if v.get("produit") == "SET" and x:
            nom = nom_set(x)
        else:
            nom = str(v.get("nom_article") or "?").removeprefix("LEGO ").capitalize()
        hv.append({"id": v["id"], "d": v["date_vente"], "p": str(v.get("plateforme") or "").title(),
                   "k": str(v.get("produit") or "").lower(), "n": nom, "q": v.get("quantite") or 1,
                   "px": v.get("prix_vente"), "co": 1 if v.get("co_investisseur") else 0,
                   "a": v.get("article_id")})
    return {"maj": maintenant().isoformat(timespec="seconds"), "achats": ha, "ventes": hv, "depenses": depenses()}


def depenses() -> dict:
    """Montant payé (sa part) par mois sur TOUS les achats de la base (pas seulement les derniers affichés), et total depuis le début."""
    dates = {a["id"]: str(a["date_achat"] or "")[:7] for a in base.lire("achats", "select=id,date_achat&limit=100000")}
    mois = {}
    for x in base.lire("articles", "limit=100000&select=achat_id,prix_achat,prix_total"):
        m = dates.get(x["achat_id"])
        if m:
            mois[m] = mois.get(m, 0) + float(x.get("prix_achat") or 0)  # sa part réelle (co-investissement exclu)
    mois = {m: round(v, 2) for m, v in sorted(mois.items())}
    return {"total": round(sum(mois.values()), 2), "debut": next(iter(mois), None), "mois": mois}


def seuils() -> dict | None:
    """Seuils de couleur du gain net (vert / rouge), les mêmes que sur Telegram."""
    try:
        import alertes
        from referentiel import charger
        return alertes.seuils_gain(charger())
    except Exception as e:
        print(f"seuils de gain indisponibles : {e}")
        return None


def main():
    obj = objectifs()
    etat = {"maj": maintenant().isoformat(timespec="seconds"), "automatisations": automatisations(),
            "tresorerie": base.tresorerie(), "objectifs": obj, "a_faire": a_regulariser(obj), "deals": deals(),
            "origines": origines(), "baisses": baisses(), "seuils_gain": seuils()}
    # Le document de l'application est limité à 256 Kio : on retire les vignettes
    # des deals les plus anciens tant qu'il dépasse 230 Ko.
    for d in reversed(etat["deals"]):
        if len(json.dumps(etat, ensure_ascii=False, default=str).encode()) <= 230_000:
            break
        d["img"] = None
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps(etat, ensure_ascii=False, default=str))
    print(f"{SORTIE} : {len(etat['automatisations'])} automatisations, {len(etat['a_faire'])} à régulariser, "
          f"{len(etat['deals'])} deals, {SORTIE.stat().st_size} octets")
    h = historique()
    # Exemplaires en stock (reçus, non vendus) tirés de la base à chaque passage :
    # l'inventaire de l'application compare les comptages à ces quantités.
    import a_publier
    a_publier.main()
    h["ex"] = json.loads(a_publier.EXEMPLAIRES.read_text())
    # Lecture de la base ratée (0 achat ou stock vide) : on garde le fichier précédent
    # plutôt que de montrer un stock à zéro dans l'inventaire.
    if not h.get("achats") or not h.get("ex"):
        raise SystemExit("historique : lecture de la base incomplète, fichier précédent conservé")
    (SORTIE.parent / "historique.json").write_text(json.dumps(h, ensure_ascii=False, default=str))
    print(f"historique : {len(h['achats'])} achats, {len(h['ventes'])} ventes, "
          f"{(SORTIE.parent / 'historique.json').stat().st_size} octets")


if __name__ == "__main__":
    main()
