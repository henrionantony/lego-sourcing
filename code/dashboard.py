"""Données de la page Dashboard de LEGO HQ (22/11/2026).

Patrimoine = apport propre + gains nets réinvestis (rien n'a été retiré pour soi : hypothèse notée dans la page).
Apport propre : total dans parametres.json (FONDS_PROPRES_TOTAL) ; les achats marqués « FONDS PROPRES »
comptent à leur date, le reste est placé sur les tout premiers achats (marquage commencé en juillet 2024).
Moteur des intérêts composés sur 12 mois : marge nette par tour (gain / coût des articles vendus) et
nombre de tours (coût des articles vendus / stock moyen au prix d'achat).
Les montants restent dans la base privée (document de l'application), jamais dans le dépôt public.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

import base
from regles import PRELEVEMENTS

ICI = Path(__file__).resolve().parent
PARAMS = ICI.parent / "data" / "parametres.json"
APUB = ICI.parent / "stock" / "app" / "a_publier.json"


def _tout(table: str, sel: str, extra: str = "") -> list:
    rows, off = [], 0
    while True:
        r = base.lire(table, f"select={sel}{extra}&order=id&limit=1000&offset={off}") or []
        rows += r
        off += 1000
        if len(r) < 1000:
            return rows


def _mois_suivant(k: str) -> str:
    a, m = int(k[:4]), int(k[5:7])
    return f"{a + 1}-01" if m == 12 else f"{a}-{m + 1:02d}"


def calculer() -> dict:
    import etat_app
    ventes = _tout("ventes", "id,date_vente,prix_vente,revenu_net,article_id,produit,nom_article,quantite,plateforme",
                   "&date_vente=not.is.null")
    par_vente: dict = {}
    etat_app.gain_net(ventes, par_vente)
    achats = {a["id"]: a for a in _tout("achats", "id,date_achat,financement")}
    arts = _tout("articles", "id,achat_id,prix_achat,offert,co_investisseur")

    m = defaultdict(lambda: defaultdict(float))
    plat = defaultdict(lambda: defaultdict(float))
    for v in ventes:
        k = v["date_vente"][:7]
        e, c, _ = par_vente.get(v["id"], (0.0, 0.0, True))
        m[k]["ca"] += float(v["prix_vente"] or 0)
        m[k]["gain"] += e - c
        m[k]["cogs"] += c
        plat[k][str(v.get("plateforme") or "AUTRE").split(" (")[0]] += float(v["prix_vente"] or 0)
    cout_achat = defaultdict(float)
    for a in arts:
        if not a.get("offert"):
            cout_achat[a["achat_id"]] += float(a.get("prix_achat") or 0) * (0.5 if a.get("co_investisseur") else 1)
    for aid, c in cout_achat.items():
        d = (achats.get(aid) or {}).get("date_achat")
        if d:
            m[d[:7]]["achats"] += c

    # Apport propre réparti dans le temps
    total = float(json.loads(PARAMS.read_text()).get("FONDS_PROPRES_TOTAL") or 0)
    marques = sorted((a["date_achat"], cout_achat.get(a["id"], 0)) for a in achats.values()
                     if a.get("date_achat") and "PROPRE" in str(a.get("financement") or "").upper())
    reste = max(0.0, total - sum(c for _, c in marques))
    apport = defaultdict(float)
    for a in sorted(achats.values(), key=lambda a: str(a.get("date_achat") or "9")):
        if reste <= 0 or not a.get("date_achat"):
            break
        part = min(reste, cout_achat.get(a["id"], 0))
        apport[a["date_achat"][:7]] += part
        reste -= part
    for d, c in marques:
        apport[d[:7]] += c

    debut = min(m)
    fin = dt.date.today().strftime("%Y-%m")
    serie, k = [], debut
    cum_g = cum_a = stock = poche = 0.0
    while k <= fin:
        x = m.get(k, {})
        cum_g += x.get("gain", 0)
        cum_a += apport.get(k, 0)
        stock += x.get("achats", 0) - x.get("cogs", 0)
        # Argent sorti de ta poche au minimum : le stock que les gains ne couvrent pas (la caisse ne descend pas sous 0)
        poche = max(poche, cum_a, stock - cum_g)
        serie.append({"k": k, "ca": round(x.get("ca", 0)), "gain": round(x.get("gain", 0)), "cogs": round(x.get("cogs", 0)),
                      "achats": round(x.get("achats", 0)), "apport": round(poche), "declare": round(cum_a), "gains": round(cum_g),
                      "stock": round(stock), "pf": {p: round(v) for p, v in plat.get(k, {}).items()}})
        k = _mois_suivant(k)

    # Moteur sur les 12 derniers mois complets + le mois en cours
    dern = serie[-12:]
    g12 = sum(x["gain"] for x in dern)
    c12 = sum(x["cogs"] for x in dern)
    stock_moy = sum(x["stock"] for x in dern) / max(1, len(dern))
    marge = g12 / c12 if c12 else 0
    tours = c12 / stock_moy if stock_moy else 0
    rendement = (1 + marge) ** tours - 1 if tours else 0
    patrimoine = serie[-1]["apport"] + serie[-1]["gains"]
    t = base.tresorerie() or {}

    return {"maj": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "serie": serie,
            "apport_total": round(total), "patrimoine": round(patrimoine), "poche": serie[-1]["apport"],
            "livret": round(float(t.get("a_rendre") or 0)), "cash": round(float(t.get("solde") or 0)),
            "moteur": {"marge": round(marge, 4), "tours": round(tours, 2), "rendement": round(rendement, 4),
                       "gain12": round(g12), "cogs12": round(c12), "stock_moy": round(stock_moy)},
            "stock": stock_actuel(), "garder": garder_vendre(rendement)}


def stock_actuel() -> dict:
    ap = json.loads(APUB.read_text())
    co = {a["id"] for a in _tout("articles", "id", "&co_investisseur=not.is.null")}
    lignes = _tout("stock", "id,id_set,categorie,gamme,prix_achat,quantite,reste,offert,date_achat,strategie", "&reste=gt.0")
    today = dt.date.today()
    tot = {"cout": 0.0, "revente": 0.0, "garde": 0.0, "boites": 0, "sans_prix": 0.0}
    age = defaultdict(float)
    gammes = defaultdict(lambda: defaultdict(float))
    for r in lignes:
        part = 0.5 if r["id"] in co else 1.0
        u = 0.0 if r.get("offert") else float(r.get("prix_achat") or 0) / max(1, int(r.get("quantite") or 1))
        q = float(r.get("reste") or 0)
        c = u * q * part
        tot["cout"] += c
        d = r.get("date_achat")
        if d:
            j = (today - dt.date.fromisoformat(d[:10])).days
            age["moins de 3 mois" if j < 90 else "3 à 12 mois" if j < 365 else "plus d'un an"] += c
        g = str(r.get("gamme") or "Autre").title()
        gammes[g]["cout"] += c
        if r.get("categorie") == "SET" and (r.get("strategie") or "ENTIER") == "ENTIER":
            tot["boites"] += int(q)
            v = ap.get(str(r.get("id_set"))) or {}
            p = v.get("prix")
            if p:
                tot["revente"] += p * q * part
                tot["garde"] += max(p, float(v.get("garde") or 0)) * q * part
                gammes[g]["revente"] += p * q * part
            else:
                tot["sans_prix"] += c
                tot["revente"] += c
                tot["garde"] += c
                gammes[g]["revente"] += c
        else:
            tot["revente"] += c   # lots, figurines, part-out : comptés au prix d'achat (prudent)
            tot["garde"] += c
            gammes[g]["revente"] += c
    top = sorted(gammes.items(), key=lambda kv: -kv[1]["cout"])[:12]
    return {**{k: round(v) for k, v in tot.items()}, "age": {k: round(v) for k, v in age.items()},
            "gammes": [{"g": g, "cout": round(x["cout"]), "revente": round(x["revente"])} for g, x in top]}


def garder_vendre(rendement: float) -> list:
    """Sets entiers dont la valeur après retrait dépasse le prix d'aujourd'hui : vendre maintenant
    (et réinvestir au rendement réel) ou attendre ? Attente = jusqu'au retrait + 6 mois."""
    ap = json.loads(APUB.read_text())
    today = dt.date.today()
    rm = (1 + rendement) ** (1 / 12) - 1
    out = []
    for s, v in ap.items():
        p, g, cout, ret = v.get("prix"), float(v.get("garde") or 0), v.get("cout"), v.get("retrait")
        if not p or not cout or g <= p or not ret:
            continue
        try:
            mois = max(1, round(((dt.date.fromisoformat(ret[:10]) - today).days / 30.4) + 6))
        except ValueError:
            continue
        maintenant = p * (1 - PRELEVEMENTS)
        vendre = maintenant * (1 + rm) ** mois - cout       # vendu tout de suite, argent réinvesti
        garder = g * (1 - PRELEVEMENTS) - cout              # vendu après le retrait
        out.append({"set": s, "prix": p, "garde": round(g), "cout": round(float(cout), 2), "mois": mois,
                    "vendre": round(vendre), "garder": round(garder), "choix": "garder" if garder > vendre else "vendre"})
    out.sort(key=lambda x: -abs(x["garder"] - x["vendre"]))
    return out[:40]


if __name__ == "__main__":
    d = calculer()
    print(json.dumps({k: d[k] for k in ("apport_total", "patrimoine", "moteur", "stock")}, ensure_ascii=False, indent=1))
    print(len(d["serie"]), "mois,", len(d["garder"]), "sets à arbitrer :",
          sum(1 for x in d["garder"] if x["choix"] == "vendre"), "à vendre")
