"""Référentiel de prix : catalogue, prix publics, retraits, valeur marché.

Sources :
- Rebrickable : catalogue complet (dumps CSV publics, sans clé).
- Brickset : prix public Lego.com (DE en euros, sinon UK/US convertis),
  dates de sortie et de retrait. Clé BRICKSET_API_KEY.
- BrickEconomy : valeur marché neuf. Clé BRICKECONOMY_API_KEY,
  100 appels par jour au plus, donc cache + priorité.

Le prix de vente attendu est le prix public multiplié par un coefficient
appris sur les ventes passées du vendeur (statut en vente / retiré, par gamme quand il
y a assez de ventes). Quand BrickEconomy répond, sa valeur prend le relais,
corrigée par le rapport observé entre ventes réelles et valeur BrickEconomy.

Usage : python3 referentiel.py <fichier des ventes>.xlsx
  écrit /mnt/project-files/lego/data/referentiel.csv
"""
import datetime as dt
import gzip
import json
import re
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA / "cache"
ANNEES_BRICKSET = range(2010, dt.date.today().year + 2)
BE_QUOTA_JOUR = 100
BE_RESERVE_FIGURINES = 60  # appels gardés chaque jour pour les cotes de figurines (décidé le 19/10)
# Conversion approximative des prix publics quand le prix allemand manque.
EUR_PAR_GBP, EUR_PAR_USD = 1.17, 0.93
# Gamme retenue seulement si au moins ce nombre de ventes pour l'apprendre.
MIN_VENTES_GAMME = 8
# Appels BrickLink par construction (limite BrickLink : 5 000 par jour).
BL_APPELS_MAX = 3000


RECENT_JOURS = 45   # « vu en vente » récemment chez Lego : pas retiré


def _get(url: str, data: dict | None = None, headers: dict | None = None) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, body, headers or {"User-Agent": "lego-sourcing/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def numero_base(x) -> str:
    """'10312', '10312-1', 10312.0 → '10312'."""
    s = str(x).strip()
    s = s[:-2] if s.endswith(".0") else s
    return s.split("-")[0]


# --- Rebrickable ------------------------------------------------------------

def catalogue_rebrickable(rafraichir: bool = False) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    for nom in ("sets", "themes"):
        f = CACHE / f"{nom}.csv.gz"
        if rafraichir or not f.exists():
            f.write_bytes(_get(f"https://cdn.rebrickable.com/media/downloads/{nom}.csv.gz"))
    sets = pd.read_csv(CACHE / "sets.csv.gz")
    themes = pd.read_csv(CACHE / "themes.csv.gz").rename(columns={"id": "theme_id", "name": "theme_rb"})
    sets = sets.merge(themes[["theme_id", "theme_rb"]], on="theme_id", how="left")
    sets["base"] = sets.set_num.map(numero_base)
    return sets[["set_num", "base", "name", "year", "theme_rb", "num_parts"]]


# --- Brickset ---------------------------------------------------------------

def _brickset_annee(annee: int, cle: str) -> list[dict]:
    sets, page = [], 1
    while True:
        params = {"year": str(annee), "pageSize": 500, "pageNumber": page}
        r = json.loads(_get("https://brickset.com/api/v3.asmx/getSets",
                            {"apiKey": cle, "userHash": "", "params": json.dumps(params)}))
        if r.get("status") != "success":
            raise RuntimeError(f"Brickset {annee} : {r.get('message')}")
        sets += r["sets"]
        if len(sets) >= r["matches"] or not r["sets"]:
            return sets
        page += 1


def brickset(rafraichir_annees: set[int] = frozenset()) -> pd.DataFrame:
    """Un fichier de cache par année ; on ne recharge que les années demandées
    (en pratique l'année en cours et la suivante, chaque nuit)."""
    cle = os.environ.get("BRICKSET_API_KEY")
    lignes = []
    for annee in ANNEES_BRICKSET:
        f = CACHE / f"brickset_{annee}.json.gz"
        if cle and (annee in rafraichir_annees or not f.exists()):
            f.write_bytes(gzip.compress(json.dumps(_brickset_annee(annee, cle)).encode()))
        if not f.exists():
            continue
        for s in json.loads(gzip.decompress(f.read_bytes())):
            lc = s.get("LEGOCom") or {}
            de, uk, us = (lc.get(p, {}) for p in ("DE", "UK", "US"))
            if de.get("retailPrice"):
                pp, src = de["retailPrice"], "DE"
            elif uk.get("retailPrice"):
                pp, src = uk["retailPrice"] * EUR_PAR_GBP, "UK"
            elif us.get("retailPrice"):
                pp, src = us["retailPrice"] * EUR_PAR_USD, "US"
            else:
                pp, src = None, None
            # Fin de vente Lego.com en Europe, sinon date de sortie de gamme.
            # Brickset remet dateLastAvailable à jour tant que le set est en vente :
            # une date récente veut dire « vu en vente ce jour-là », pas « retiré ».
            fins = [p.get("dateLastAvailable") for p in (de, uk) if p.get("dateLastAvailable")]
            fin = max(fins) if fins else None
            if not fin or fin[:10] >= (dt.date.today() - dt.timedelta(days=RECENT_JOURS)).isoformat():
                fin = s.get("exitDate")
            lignes.append({
                "set_num": f"{s['number']}-{s['numberVariant']}",
                "theme_bs": s.get("theme"), "categorie_bs": s.get("category"),
                "prix_public": pp, "prix_public_src": src,
                "sortie": (de.get("dateFirstAvailable") or s.get("launchDate") or "")[:10] or None,
                "retrait": (fin or "")[:10] or None,
                "minifigs": s.get("minifigs"),
                # « LEGO exclusive » : vendu seulement par Lego, souvent épuisé dès le Black Friday.
                "exclusif": "exclusive" in (s.get("availability") or "").lower(),
            })
    return pd.DataFrame(lignes).drop_duplicates("set_num")


# --- BrickEconomy -----------------------------------------------------------

class BrickEconomy:
    """Client avec cache disque et compteur journalier (100 appels/jour)."""
    URL = "https://www.brickeconomy.com/api/v1/set/{}"

    def __init__(self):
        self.cle = os.environ.get("BRICKECONOMY_API_KEY")
        self.f_cache = CACHE / "brickeconomy.json"
        self.f_quota = CACHE / "brickeconomy_quota.json"
        self.cache = json.loads(self.f_cache.read_text()) if self.f_cache.exists() else {}
        q = json.loads(self.f_quota.read_text()) if self.f_quota.exists() else {}
        self.jour = dt.date.today().isoformat()
        self.appels = q.get("appels", 0) if q.get("jour") == self.jour else 0

    def restant(self, figurines: bool = False) -> int:
        """Appels encore permis aujourd'hui : les sets laissent la réserve aux figurines."""
        plafond = BE_QUOTA_JOUR if figurines else BE_QUOTA_JOUR - BE_RESERVE_FIGURINES
        return max(0, plafond - self.appels) if self.cle else 0

    def valeur(self, set_num: str, max_age_jours: int = 7) -> dict | None:
        c = self.cache.get(set_num)
        if c and (dt.date.today() - dt.date.fromisoformat(c["date"])).days <= max_age_jours:
            return c
        if self.restant() <= 0:
            return c
        try:
            brut = json.loads(_get(self.URL.format(set_num) + "?currency=EUR",
                                   headers={"x-apikey": self.cle, "Accept": "application/json",
                                            "User-Agent": "lego-sourcing/0.1"}))
        except Exception as e:  # réseau, quota, set inconnu
            print(f"BrickEconomy {set_num} : {e}", file=sys.stderr)
            return c
        finally:
            self.appels += 1
            self.f_quota.write_text(json.dumps({"jour": self.jour, "appels": self.appels}))
        d = brut.get("data", brut)
        # Exemple brut gardé une fois par jour pour voir les champs disponibles (figurines…).
        ex = CACHE / "brickeconomy_brut_exemple.json"
        if not ex.exists() or json.loads(ex.read_text()).get("jour") != self.jour:
            ex.write_text(json.dumps({"jour": self.jour, "set": set_num, "brut": brut}))
        figs = {k: v for k, v in d.items() if "minifig" in k.lower()}
        c = {"date": self.jour, "figurines": figs or None,
             "valeur_neuf": d.get("current_value_new"),
             "valeur_occasion": d.get("current_value_used"),
             "prevision_2ans": d.get("forecast_value_new_2_years"),
             "retire": d.get("retired"),
             "retrait": d.get("retired_date"),
             "prix_public_eu": d.get("retail_price_eu"),
             # Historique de la valeur neuf : sert au test sur l'historique
             # (valeur connue le jour de l'achat).
             "historique": [(e["date"], e["value"]) for e in d.get("price_events_new") or []]}
        self.cache[set_num] = c
        self.f_cache.write_text(json.dumps(self.cache))
        return c

    def figurine(self, num: str, max_age_jours: int = 30) -> float | None:
        """Valeur neuf BrickEconomy d'une figurine (ex. « njo862 »), sur la réserve
        d'appels des figurines ; cache disque partagé avec alertes.py."""
        f = CACHE / "brickeconomy_figurines.json"
        figs = json.loads(f.read_text()) if f.exists() else {}
        c = figs.get(num)
        if c and (dt.date.today() - dt.date.fromisoformat(c["date"])).days <= max_age_jours:
            return c["valeur_neuf"]
        if self.restant(figurines=True) <= 0:
            return c and c["valeur_neuf"]
        try:
            brut = json.loads(_get(f"https://www.brickeconomy.com/api/v1/minifig/{num}?currency=EUR",
                                   headers={"x-apikey": self.cle, "Accept": "application/json",
                                            "User-Agent": "lego-sourcing/0.1"}))
        except Exception as e:
            print(f"BrickEconomy figurine {num} : {e}", file=sys.stderr)
            if "400" in str(e):  # code inconnu de BrickEconomy : on ne le redemande pas avant 30 jours
                figs[num] = {"date": self.jour, "valeur_neuf": None, "inconnue": True}
                f.write_text(json.dumps(figs))
            return c and c["valeur_neuf"]
        finally:
            self.appels += 1
            self.f_quota.write_text(json.dumps({"jour": self.jour, "appels": self.appels}))
        d = brut.get("data", brut)
        figs[num] = {"date": self.jour, "valeur_neuf": d.get("current_value_new"), "nom": d.get("name"),
                     "gamme": d.get("theme"), "sets": d.get("sets") or []}
        f.write_text(json.dumps(figs))
        return figs[num]["valeur_neuf"]


# --- Coefficients appris sur l'historique -----------------------------------

def ventes_sets_entiers(xlsx: str) -> pd.DataFrame:
    """Ventes de sets entiers neufs, avec coût unitaire et date d'achat."""
    inv = pd.read_excel(xlsx, sheet_name=0)
    ventes = pd.read_excel(xlsx, sheet_name=1).iloc[:, :28]
    inv["strat"] = inv["Strategie vente"].astype(str).str.strip()
    sets = inv[(inv.Categorie == "SET") & (inv.strat == "ENTIER") & (inv["Prix achat"] > 0)].copy()
    # Achat à deux (« AVEC <associé> (50/50) ») : l'Excel ne garde que ma part,
    # à l'achat comme à la vente. On remet le prix du set entier.
    sets["part"] = [part_achat(d, m) for d, m in zip(sets.Details, sets["ID minifig"])]
    sets["cout_unitaire"] = sets["Prix achat"] / sets["Quantite"] / sets.part
    sets["base"] = sets["ID set"].map(numero_base)
    v = ventes[ventes.Produit == "SET"].drop(columns=["Gamme"]).merge(
        sets[["ID", "base", "Designation", "Gamme", "cout_unitaire", "Date achat", "Lieu achat", "part"]],
        left_on="ID Achat", right_on="ID")
    v["Prix Vente"] = v["Prix Vente"] / v.part
    return v


def part_achat(details, minifig) -> float:
    """Ma part dans un achat partagé (1 si acheté seul). Les prénoms des
    associés viennent du fichier privé parametres.json (ASSOCIES)."""
    f = DATA / "parametres.json"
    associes = json.loads(f.read_text()).get("ASSOCIES", []) if f.exists() else []
    t = f"{details if isinstance(details, str) else ''} {minifig if isinstance(minifig, str) else ''}".upper()
    if not associes or not re.search(r"AVEC (" + "|".join(map(re.escape, associes)) + ")", t):
        return 1.0
    q = re.search(r"\((\d+)/(\d+)\)", t)
    return int(q.group(1)) / 100 if q else 0.5


def statut_a(date, sortie, retrait) -> str:
    """'en vente' ou 'retiré' à une date donnée."""
    if isinstance(retrait, str) and pd.Timestamp(retrait) < pd.Timestamp(date):
        return "retiré"
    return "en vente"


def coefficients(v: pd.DataFrame, avant=None) -> dict:
    """Médiane du rapport prix de vente / prix public, par statut du set le
    jour de l'achat (seule chose connue au moment de décider) et par gamme. `avant` limite l'apprentissage aux ventes antérieures (test
    honnête : on n'utilise que ce qui était connu le jour de l'achat)."""
    v = v[v.prix_public > 0]
    if avant is not None:
        v = v[v["Date Vente"] < avant]
    v = v.assign(ratio=v["Prix Vente"] / v.prix_public)
    coef = {"global": {s: g.ratio.median() for s, g in v.groupby("statut_achat")}, "gamme": {}}
    for (gamme, s), g in v.groupby(["Gamme", "statut_achat"]):
        if len(g) >= MIN_VENTES_GAMME:
            coef["gamme"][(gamme, s)] = g.ratio.median()
    coef["n"] = len(v)
    return coef


# Deux façons de revendre un set acheté pendant qu'il est au catalogue :
# - « rapide » : revendu tant que Lego le vend encore, donc sous le prix public
#   (rapport et délai appris sur les ventes passées) ;
# - « garde » : gardé jusqu'au retrait du catalogue puis revendu au-dessus du
#   prix public, quelques mois après le retrait.
# Un set déjà retiré n'a qu'un scénario.
MIN_VENTES_SCENARIO = 5
JOURS_APRES_RETRAIT = 120  # par défaut, si la gamme n'a pas assez de ventes
RENDEMENT_MIN_GARDE = 0.20  # par an : en dessous, garder immobilise trop la trésorerie


def coefficients_scenarios(v: pd.DataFrame) -> dict:
    """Rapport vente / prix public et délai, par scénario et par gamme."""
    v = v[(v.prix_public > 0) & (v.statut_achat == "en vente")].assign(
        ratio=lambda d: d["Prix Vente"] / d.prix_public,
        delai=lambda d: (pd.to_datetime(d["Date Vente"]) - pd.to_datetime(d["Date achat"])).dt.days,
        apres=lambda d: (pd.to_datetime(d["Date Vente"]) - pd.to_datetime(d.retrait, errors="coerce")).dt.days)
    out = {}
    for nom, statut, champ in (("rapide", "en vente", "delai"), ("garde", "retiré", "apres")):
        g = v[v.statut_vente == statut]
        par = g.groupby("Gamme").agg(n=("ratio", "size"), ratio=("ratio", "median"), delai=(champ, "median"))
        par = par[par.n >= MIN_VENTES_SCENARIO]
        out[nom] = {"global": (g.ratio.median(), g[champ].median()),
                    "gamme": {k: (x.ratio, x.delai) for k, x in par.iterrows()}}
    return out


def scenarios(prix_public, gamme, retrait, cs: dict, aujourdhui=None, set_num=None) -> dict:
    """{'rapide': (prix, jours), 'garde': (prix, jours)} pour un set au catalogue."""
    aujourdhui = aujourdhui or pd.Timestamp.today()
    out = {}
    for nom in ("rapide", "garde"):
        ratio, delai = cs[nom]["gamme"].get(gamme, cs[nom]["global"])
        if nom == "garde":
            r = pd.to_datetime(retrait, errors="coerce")
            if pd.isna(r):
                continue  # date de retrait inconnue : on ne parie pas dessus
            delai = max((r - aujourdhui).days, 0) + (delai if delai == delai else JOURS_APRES_RETRAIT)
        pv = prix_public * ratio
        if nom == "rapide":  # revendu avant le retrait : sous le prix LEGO, frais compris
            from regles import plafond_catalogue
            pv = min(pv, plafond_catalogue(prix_public, set_num) or pv)
        out[nom] = (pv, int(delai))
    return out


# Prise de valeur après le retrait (règle : toujours lire une vente par rapport à la date de
# retrait du set). Rapport vente / prix public des ventes faites APRÈS le retrait, selon l'âge de la
# retraite le jour de la vente. Les ventes faites pendant que le set était au catalogue (sous le prix
# LEGO) n'entrent pas dans la valeur d'un set retiré. La courbe ne redescend pas : les sets vendus
# tard sont souvent ceux qui traînaient (biais), pas une baisse de la cote.
TRANCHES_RETRAIT = (90, 180, 365, 730, 100000)
PIVOT_JOURS = 730  # le pivot : ~2 ans après le retrait, le stock d'occasion s'épuise


def courbe_retrait(v: pd.DataFrame) -> dict:
    """{"courbe": [(jours max, rapport)], "gamme": {gamme: facteur relatif}} sur les ventes après retrait."""
    v = v[(v.prix_public > 0) & (v.statut_vente == "retiré")].assign(
        ratio=lambda d: d["Prix Vente"] / d.prix_public,
        apres=lambda d: (pd.to_datetime(d["Date Vente"]) - pd.to_datetime(d.retrait, errors="coerce")).dt.days)
    glob, courbe, mx, debut = v.ratio.median(), [], 0.0, -1
    for fin in TRANCHES_RETRAIT:
        g = v[(v.apres > debut) & (v.apres <= fin)]
        r = g.ratio.median() if len(g) >= MIN_VENTES_SCENARIO else glob
        mx = max(mx, r)
        courbe.append((fin, mx))
        debut = fin
    par = v.groupby("Gamme").ratio.agg(["size", "median"])
    par = par[par["size"] >= MIN_VENTES_SCENARIO]
    return {"courbe": courbe, "gamme": {g: x / glob for g, x in par["median"].items()}}


def rapport_retrait(cr: dict, gamme, jours: float) -> float:
    r = next((x for fin, x in cr["courbe"] if jours <= fin), cr["courbe"][-1][1])
    return r * cr["gamme"].get(gamme, 1.0)


# Erreur médiane de chaque source sur les ventes passées (backtest_estime.py).
# Le prix attendu est la moyenne des sources disponibles, chacune pondérée par
# 1 / erreur² : la plus précise pèse le plus, aucune n'est jetée.
ERREURS = {"BrickLink": 0.15, "gamme": 0.11, "global": 0.19, "BrickEconomy": 0.18}


def estimer(prix_public, statut: str, gamme, coef: dict, valeur_be=None, ratio_be=None,
            valeur_bl=None, ratio_bl=None, erreurs: dict = ERREURS, k_retrait=None):
    """Prix de vente attendu (avant commissions) et sa fiabilité.
    Sources : ventes conclues BrickLink en Europe, prix public × rapport
    appris par gamme, valeur BrickEconomy (surtout marché anglophone).
    BrickLink et BrickEconomy sont corrigés par le rapport observé entre les
    ventes passées et la source (correcteur France). La fiabilité retenue est
    celle de la source la plus précise utilisée."""
    sources = {}
    if valeur_bl and not pd.isna(valeur_bl):
        sources["BrickLink"] = valeur_bl * (ratio_bl or 1.0)
    if prix_public and not pd.isna(prix_public):
        k = coef["gamme"].get((gamme, statut))
        f = "gamme" if k else "global"
        k = k or coef["global"].get(statut) or coef["global"].get("en vente")
        if k_retrait:  # set retiré : rapport appris selon l'âge de sa retraite
            k = k_retrait
        if k:
            sources[f] = prix_public * k
    if valeur_be and not pd.isna(valeur_be):
        sources["BrickEconomy"] = valeur_be * (ratio_be or 1.0)
    if not sources:
        return None, "non chiffré"
    poids = {s: 1 / erreurs[s] ** 2 for s in sources}
    prix = sum(sources[s] * poids[s] for s in sources) / sum(poids.values())
    f = min(sources, key=lambda s: erreurs[s])
    if "BrickEconomy" in sources and len(sources) == 2 and f != "BrickLink":
        f = "mixte"
    return prix, f


# Demande France Dealabs : une gamme dont les deals Lego chauffent peu
# (température médiane sous SEUIL_FROID, au moins 5 deals) se revend moins
# bien que prévu (rapport mesuré sur les ventes passées).
SEUIL_FROID = 70
DEMANDE_FROIDE = 0.85


def chaleur_dealabs(ref: pd.DataFrame, min_deals: int = 5) -> dict:
    """Température médiane des deals Dealabs par thème Rebrickable."""
    f = DATA / "dealabs_deals.csv"
    if not f.exists():
        return {}
    d = pd.read_csv(f, dtype={"set": str})
    d["theme"] = d.set.map(ref.drop_duplicates("base").set_index("base").theme_rb)
    t = d.groupby("theme").temperature.agg(["median", "size"])
    return t[t["size"] >= min_deals]["median"].to_dict()


def joindre(v: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    r = ref.sort_values("set_num").drop_duplicates("base")  # variante -1 d'abord
    v = v.merge(r[["base", "set_num", "prix_public", "sortie", "retrait"]], on="base", how="left")
    v["statut_vente"] = [statut_a(d, s, r) for d, s, r in zip(v["Date Vente"], v.sortie, v.retrait)]
    v["statut_achat"] = [statut_a(d, s, r) for d, s, r in zip(v["Date achat"], v.sortie, v.retrait)]
    return v


def construire(xlsx: str, rafraichir: bool = False) -> pd.DataFrame:
    an = dt.date.today().year
    rb = catalogue_rebrickable(rafraichir)
    # Dates de retrait : Brickset les met à jour au fil de l'année, y compris pour
    # les sets sortis il y a plusieurs années, donc on recharge les 5 dernières.
    bs = brickset(set(range(an - 4, an + 2)) if rafraichir else set())
    ref = rb.merge(bs, on="set_num", how="left")
    ref = ref[ref.year >= ANNEES_BRICKSET.start]

    v = joindre(ventes_sets_entiers(xlsx), ref)
    coef = coefficients(v)
    # Gamme du vendeur la plus fréquente par thème Rebrickable, pour réutiliser
    # les coefficients par gamme sur les sets qu'il n'a jamais achetés.
    gamme_par_theme = (v.merge(ref[["set_num", "theme_rb"]], on="set_num")
                       .groupby("theme_rb").Gamme.agg(lambda g: g.mode().iat[0]).to_dict())

    be = BrickEconomy()
    # Priorité BrickEconomy : sets encore en vente ou retirés depuis moins de
    # deux ans, dans l'ordre des ventes passées puis du prix public.
    aujourdhui = pd.Timestamp.today()
    ref["statut"] = [statut_a(aujourdhui, s, r) for s, r in zip(ref.sortie, ref.retrait)]
    recents = ref[ref.prix_public.notna() & (pd.to_datetime(ref.retrait, errors="coerce")
                                              .fillna(aujourdhui) > aujourdhui - pd.DateOffset(years=2))]
    deja_vendus = list(v.set_num.dropna().value_counts().index)
    pp = recents.set_index("set_num").prix_public
    # Sets à vérifier en premier (ex. candidats d'une promo Lego.com), un par ligne.
    prio = DATA / "brickeconomy_prioritaires.txt"
    prio = [l.strip() for l in prio.read_text().split()] if prio.exists() else []
    # Puis ton stock (pour bien le prix de tes annonces), d'abord les sets jamais relevés
    try:
        stock = [s + "-1" for s in json.loads((DATA.parent / "stock" / "app" / "exemplaires.json").read_text())]
    except (OSError, ValueError):
        stock = []
    stock = sorted(stock, key=lambda s: s in be.cache)
    ordre = list(dict.fromkeys(prio + stock + deja_vendus
                               + sorted(set(recents.set_num) - set(deja_vendus), key=lambda s: -pp.get(s, 0))))
    # Rafraîchir une valeur au bout de 30 jours suffit : les appels servent à couvrir plus de sets
    for s in ordre:
        if be.restant() <= 0:
            break
        be.valeur(s, max_age_jours=7 if s in prio else 30)

    # Cotes des figurines des sets déjà relevés (scénario « découpe » des alertes),
    # dans le même ordre de priorité, sur la réserve d'appels des figurines.
    # (figurines en stock et les plus vendues d'abord : voir cotes_figs.py)
    import cotes_figs
    cotes_figs.main(be)

    # Rapport ventes réelles / valeur BrickEconomy, quand on en a assez.
    v["valeur_be"] = v.set_num.map(lambda s: (be.cache.get(s) or {}).get("valeur_neuf"))
    # La valeur BrickEconomy est celle d'aujourd'hui : on la compare aux ventes des 18 derniers mois,
    # séparément pour les sets vendus encore au catalogue (sous la cote) et après leur retrait.
    vb = v[(v.valeur_be > 0) & (pd.to_datetime(v["Date Vente"]) > pd.Timestamp.today() - pd.DateOffset(months=18))]
    ratio_be = (vb["Prix Vente"] / vb.valeur_be).median() if len(vb) >= 20 else 1.0
    ratios_be = {s: (g["Prix Vente"] / g.valeur_be).median() if len(g) >= 20 else ratio_be
                 for s, g in vb.groupby("statut_vente")}

    # BrickLink : ventes conclues en Europe, mêmes priorités, 5 000 appels/jour.
    import bricklink
    bl = bricklink.BrickLink()
    for s in ordre[:BL_APPELS_MAX]:
        if not bl.actif():
            break
        bl.prix(s)
    ref["valeur_bl"] = ref.set_num.map(lambda s: bricklink.valeur(bl.cache.get(f"{s}|sold")))
    v["valeur_bl"] = v.set_num.map(ref.set_index("set_num").valeur_bl)
    vl = v[v.valeur_bl > 0]
    ratio_bl = (vl["Prix Vente"] / vl.valeur_bl).median() if len(vl) >= 20 else 1.0

    cr = courbe_retrait(v)
    age = (aujourdhui - pd.to_datetime(ref.retrait, errors="coerce")).dt.days
    ref["jours_retrait"] = age.where(ref.statut == "retiré")
    k_ret = [rapport_retrait(cr, gamme_par_theme.get(th), a) if st == "retiré" and a == a else None
             for st, th, a in zip(ref.statut, ref.theme_rb, age)]
    est = [estimer(pp, st, gamme_par_theme.get(th), coef,
                   (be.cache.get(s) or {}).get("valeur_neuf"), ratios_be.get(st, ratio_be), vbl, ratio_bl, k_retrait=kr)
           for pp, st, th, s, vbl, kr in zip(ref.prix_public, ref.statut, ref.theme_rb, ref.set_num,
                                             ref.valeur_bl, k_ret)]
    # Prise de valeur attendue au pivot (2 ans après le retrait), pour les sets retirés ou à date de retrait connue
    ref["pv_pivot"] = [round(pp * rapport_retrait(cr, gamme_par_theme.get(th), PIVOT_JOURS), 2)
                       if pp == pp and pp and r == r else None
                       for pp, th, r in zip(ref.prix_public, ref.theme_rb, pd.to_datetime(ref.retrait, errors="coerce"))]
    ref["pivot_le"] = (pd.to_datetime(ref.retrait, errors="coerce") + pd.Timedelta(days=PIVOT_JOURS)).dt.strftime("%Y-%m-%d")
    ref["chaleur_dealabs"] = ref.theme_rb.map(chaleur_dealabs(ref))
    # Seulement pour les sets encore au catalogue : une fois retiré, c'est la rareté qui fait le prix,
    # plus la chaleur des promos Dealabs (qui ne parlent que des sets encore vendus).
    froid = (ref.chaleur_dealabs < SEUIL_FROID) & (ref.statut != "retiré")
    ref["prix_vente_estime"] = [e[0] * (DEMANDE_FROIDE if fr else 1) if e[0] else None
                                for e, fr in zip(est, froid)]
    ref["fiabilite"] = [e[1] for e in est]

    # Délai de revente habituel du vendeur (achat → vente), par gamme quand il
    # y a au moins 5 ventes, sinon médiane globale. Il inclut les sets gardés
    # volontairement en attendant le retrait : c'est son rythme réel.
    v["delai"] = (pd.to_datetime(v["Date Vente"]) - pd.to_datetime(v["Date achat"])).dt.days
    dg = v.groupby("Gamme").delai.agg(["count", "median"])
    dg = dg[dg["count"] >= 5]["median"].to_dict()
    ref["gamme"] = ref.theme_rb.map(gamme_par_theme)
    ref["delai_jours"] = ref.gamme.map(dg).fillna(v.delai.median()).round()
    ref["deja_vendus"] = ref.base.map(v.base.value_counts()).fillna(0).astype(int)

    from regles import net, prix_max
    # Sets au catalogue : deux scénarios, on garde celui qui permet de payer le
    # plus cher, « garde » seulement si son rendement annuel suffit.
    cs = coefficients_scenarios(v)
    cols = {"pv_rapide": [], "j_rapide": [], "pv_garde": [], "j_garde": []}
    for pp, st, g, rt, fr, b in zip(ref.prix_public, ref.statut, ref.gamme, ref.retrait, froid, ref.base):
        sc = scenarios(pp, g, rt, cs, set_num=b) if st == "en vente" and pp == pp and pp else {}
        k = DEMANDE_FROIDE if fr else 1
        for nom in ("rapide", "garde"):
            pv, j = sc.get(nom, (None, None))
            cols[f"pv_{nom}"].append(round(pv * k, 2) if pv else None)
            cols[f"j_{nom}"].append(j)
    for c, l in cols.items():
        ref[c] = l
    pm = []
    for pv, pr, jr, pg, jg in zip(ref.prix_vente_estime, ref.pv_rapide, ref.j_rapide, ref.pv_garde, ref.j_garde):
        if pr == pr and pr:
            m = prix_max(pr)
            if pg == pg and pg:
                mg = prix_max(pg)
                if mg > m and net(pg, mg) / mg * 365 / max(jg, 30) >= RENDEMENT_MIN_GARDE:
                    m = mg
                # Le rendement de « garde » au prix max peut être sous le seuil alors
                # qu'à un prix plus bas il passe : on cherche le prix qui l'atteint.
                elif mg > m:
                    lo, hi = m, mg
                    for _ in range(30):
                        mid = (lo + hi) / 2
                        ok = net(pg, mid) / mid * 365 / max(jg, 30) >= RENDEMENT_MIN_GARDE
                        lo, hi = (mid, hi) if ok else (lo, mid)
                    m = lo
            pm.append(round(m, 2))
        else:
            pm.append(round(prix_max(pv), 2) if pv == pv and pv else None)
    ref["prix_max"] = pm
    ref["prix_vente_estime"] = ref.prix_vente_estime.round(2)
    DATA.mkdir(exist_ok=True)
    ref.to_csv(DATA / "referentiel.csv", index=False)
    print(f"{len(ref)} sets, {ref.prix_public.notna().sum()} avec prix public, "
          f"{ref.fiabilite.isin(['BrickEconomy', 'mixte']).sum()} avec valeur BrickEconomy, "
          f"{ref.valeur_bl.notna().sum()} avec ventes BrickLink Europe (correcteur ×{ratio_bl:.2f}), "
          f"{ref.prix_max.notna().sum()} avec prix max. Coefficients sur {coef['n']} ventes : "
          + ", ".join(f"{k} ×{x:.2f}" for k, x in coef["global"].items())
          + ". Après retrait : " + ", ".join(f"≤{f} j ×{x:.2f}" for f, x in cr["courbe"])
          + ". BrickEconomy : " + ", ".join(f"{k} ×{x:.2f}" for k, x in ratios_be.items()))
    return ref


def charger() -> pd.DataFrame:
    return pd.read_csv(DATA / "referentiel.csv", dtype={"base": str}, low_memory=False)


if __name__ == "__main__":
    construire(sys.argv[1], rafraichir="--rafraichir" in sys.argv)
