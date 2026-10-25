"""Alertes de sourcing : lit les nouveaux deals Lego de Dealabs, juge chaque
set avec le référentiel et prévient sur Telegram.

Note A (« achète ») et raretés : message qui sonne, avec lien direct vers le marchand. Le reste : message silencieux.
« non » : journalisé seulement. Tout est noté dans ../data/journal_deals.csv.

Variables : TELEGRAM_BOT_TOKEN (bot créé avec @BotFather) ; TELEGRAM_CHAT_ID
facultatif, sinon pris du dernier message envoyé au bot.
Usage : python3 alertes.py [--essai]   (--essai : affiche sans envoyer)
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import pandas as pd

import base
import regles
from dealabs import UA
from dealabs_groupe import RE_THREAD, champ, extraire_set
import operations
from promo_lego import detecter, message as message_promo
from referentiel import CACHE, DATA, charger
from regles import MAX_OPPORTUNITE, PART_MAX_GAMME, PART_MAX_SET, note_deal, MARGE_NETTE_MIN, PRELEVEMENTS, QUANTITE_MAX_GARDE, caisse_utilisable, plafond_garde, garde_possible, juger_set, net, quantite, risque

VUS = CACHE / "alertes_vus.json"
JOURNAL = DATA / "journal_deals.csv"
def _compact(x) -> str:
    return re.sub(r"[^a-z0-9]", "", str(x).lower())


def corriger_set(titre: str, s: str | None, ref) -> tuple[str | None, str | None]:
    """Numéro mal tapé dans le titre (ex. « 75423 K2SO » pour 75434) : si le nom d'un set au
    numéro proche (deux chiffres au plus) figure dans le titre et aucun mot du nom du numéro
    tapé, on calcule pour ce set-là et on renvoie un avertissement à afficher."""
    if not s:
        return s, None
    t = _compact(re.split(r"[,(]", titre)[0])  # le nom du set, pas la liste des minifigs qui suit
    lignes = ref.drop_duplicates("base").assign(base=lambda r: r.base.astype(str))
    noms = dict(zip(lignes.base, lignes.name))
    annees = dict(zip(lignes.base, lignes.year)) if "year" in lignes else {}
    propre = _compact(noms.get(s, ""))
    if not propre or any(len(m) >= 4 and m in t for m in map(_compact, str(noms[s]).split())):
        return s, None

    def proche(v):  # au plus deux chiffres différents, même début de numéro
        return len(v) == len(s) and v[:2] == s[:2] and sum(a != b for a, b in zip(v, s)) <= 2
    trouves = [v for v, n in noms.items() if v != s and proche(v) and len(_compact(n)) >= 4 and _compact(n) in t
               and _compact(n) not in propre and propre not in _compact(n)]
    if not trouves:
        return s, None
    v = max(trouves, key=lambda v: annees.get(v) or 0)  # le plus récent : les deals portent sur le catalogue
    return v, f"⚠️ Numéro corrigé : le titre dit {s} ({noms[s]}) mais parle de {noms[v]} → calcul pour {v}, vérifie"


# Reste d'un set découpé (sans ses figurines) : compté à 50 % de sa cote, par prudence
REST_DECOUPE = 0.5


def deals_recents() -> list[dict]:
    # La page du groupe est triée par popularité par défaut : sans sortBy=new on
    # revoyait les mêmes 30 deals et aucun nouveau n'arrivait. On lit les deux.
    blocs = []
    for tri in ("?sortBy=new", "?page=1"):
        req = urllib.request.Request("https://www.dealabs.com/groupe/lego" + tri,
                                     headers={"User-Agent": UA, "Accept-Language": "fr-FR"})
        blocs += RE_THREAD.findall(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))
    deals, ids = [], set()
    for b in blocs:
        i = champ(b, "threadId", r'"?(\d+)"?')
        if i in ids:
            continue
        ids.add(i)
        titre = json.loads('"' + champ(b, "title", r'"((?:[^"\\]|\\.)*)"') + '"')
        prix = champ(b, "price")
        if champ(b, "isExpired", r'(true|false)') == "true":
            continue
        marchand = champ(b, "merchantName", r'"((?:[^"\\]|\\.)*)"')
        if marchand:
            titre += f" ({json.loads(chr(34) + marchand + chr(34))})"
        deals.append({"id": champ(b, "threadId", r'"?(\d+)"?'), "titre": titre,
                      "prix": float(prix) if prix and float(prix) > 0 else None,
                      "temperature": float(champ(b, "temperature") or 0),
                      "lien": lien_dealabs(champ(b, "threadId", r'"?(\d+)"?'), champ(b, "titleSlug", r'"([\w-]*)"'))})
    return deals


def lien_dealabs(id_deal, slug="") -> str:
    """Dealabs veut « <titre>-<id> » : l'id seul donne une page d'erreur, un titre
    approximatif redirige vers le bon deal."""
    return f"https://www.dealabs.com/bons-plans/{slug or 'deal'}-{id_deal}"


def github_surveille() -> bool:
    """Vrai si le passage GitHub (toutes les 30 secondes) tourne en ce moment : c'est
    lui qui envoie sur Telegram. Le passage cloud ne fait alors que noter les deals
    pour l'application, sinon chaque deal arrive deux fois."""
    if os.environ.get("GITHUB_ACTIONS"):
        return False
    try:
        from etat_app import github_runs
        if not any(r["status"] == "in_progress" for r in github_runs() or []):
            return False
        # GitHub tourne mais plante (ex. 21/10 : colonne manquante) : on envoie nous-mêmes
        dernieres = base.lire("executions", "select=ok&tache=eq.alertes&fin=not.is.null&order=id.desc&limit=3")
        return not dernieres or any(e["ok"] for e in dernieres)
    except Exception:
        return False  # statut inconnu : mieux vaut un doublon qu'une alerte perdue


MUET = False


def telegram(texte: str, sonne: bool, essai: bool):
    if MUET and not essai:
        return
    if essai:
        print(("🔔 " if sonne else "   ") + texte.replace("\n", " | "))
        return
    jeton = os.environ["TELEGRAM_BOT_TOKEN"]
    api = f"https://api.telegram.org/bot{jeton}/"
    # Destinataire fixé (variable d'environnement ou fichier privé) : jamais déduit des
    # messages reçus par le bot, sinon n'importe qui pourrait détourner les alertes.
    chat = os.environ.get("TELEGRAM_CHAT_ID") or regles.TELEGRAM_CHAT_ID
    if not chat:
        sys.exit("TELEGRAM_CHAT_ID manquant (fichier privé parametres.json)")
    data = urllib.parse.urlencode({"chat_id": chat, "text": texte,
                                   "disable_notification": "false" if sonne else "true"}).encode()
    urllib.request.urlopen(api + "sendMessage", data=data, timeout=30)


_PHARES: dict | None = None


def gammes_phares() -> dict[str, float]:
    """Part des ventes des 12 derniers mois par gamme, pour les gammes au-dessus de
    regles.PART_GAMME_PHARE (lu dans la base, jamais écrit dans le code)."""
    global _PHARES
    if _PHARES is None:
        _PHARES = {}
        debut = (dt.date.today() - dt.timedelta(days=365)).isoformat()
        v = base.lire("ventes", f"select=article_id&date_vente=gte.{debut}&limit=10000")
        ids = ",".join({str(x["article_id"]) for x in v if x.get("article_id")})
        if ids:
            g = {a["id"]: str(a["gamme"] or "").strip().upper()
                 for a in base.lire("articles", f"select=id,gamme&id=in.({ids})&limit=10000")}
            compte: dict[str, int] = {}
            for x in v:
                k = g.get(x.get("article_id"))
                if k and k != "LEGO":
                    compte[k] = compte.get(k, 0) + 1
            _PHARES = {k: n / len(v) for k, n in compte.items() if n / len(v) >= regles.PART_GAMME_PHARE}
    return _PHARES


def euros(x: float) -> str:
    return f"{x:.2f} €".replace(".", ",")


def duree(j: int) -> str:
    return f"{j} jours" if j < 60 else f"{j / 30:.0f} mois"


_FIGS: dict = {}


def decoupe(x, prix: float) -> str | None:
    """Scénario « découpe » : figurines revendues à part (cote BrickEconomy × ton ratio
    de vente des figurines dans la gamme) + le reste du set compté prudemment à
    REST_DECOUPE de sa cote. Seulement avec les cotes déjà en cache (aucun appel ici)."""
    if not _FIGS:
        f = CACHE / "brickeconomy.json"
        _FIGS["sets"] = json.loads(f.read_text()) if f.exists() else {}
        f = CACHE / "brickeconomy_figurines.json"
        _FIGS["figs"] = json.loads(f.read_text()) if f.exists() else {}
        # Ton prix de vente des figurines / leur cote BrickEconomy (même source que les cotes)
        import cotes_figs
        _FIGS["ratio"] = {g: r for g, (r, n) in cotes_figs.coefficient().items() if n >= 20}
    st = _FIGS["sets"].get(str(x.set_num)) or {}
    q = (st.get("figurines") or {}).get("minifigs_quantities") or {}
    cotes = [((_FIGS["figs"].get(n) or {}).get("valeur_neuf"), k) for n, k in q.items()]
    if not q or not st.get("valeur_neuf") or any(not c for c, _ in cotes):
        return None
    figs = sum(c * k for c, k in cotes)
    g = str(x.get("gamme", "")).strip().upper()
    ratio = next((r for k, r in _FIGS["ratio"].items() if k != "TOUTES" and k.upper() in g), _FIGS["ratio"].get("TOUTES"))
    if not ratio:
        return None
    reste = max(0.0, st["valeur_neuf"] - figs) * REST_DECOUPE
    pv = figs * ratio + reste
    gn = net(pv, prix)
    return (f"✂ Découpe : {len(q)} figurines cotées {euros(figs)} × {str(ratio).replace('.', ',')} (tes ventes) "
            f"+ reste du set {euros(reste)} → vers {euros(pv)}, gain net {euros(gn)} ({gn / prix:+.0%})")


_EBAY = None


def prix_ebay() -> dict:
    """Prix demandés sur eBay.fr par set (document privé « ebay/prix », écrit par ebay.py)."""
    global _EBAY
    if _EBAY is None:
        try:
            _EBAY = base.doc("ebay/prix") or {}
        except Exception:
            _EBAY = {}
    return _EBAY


def details(s: str, prix: float, ref, tresor=None, en_stock: int = 0, part_out=None, note: dict | None = None,
            engage: dict | None = None) -> list[str]:
    """Lignes d'aide à la décision : scénarios de revente, risque, quantité.
    Si `note` est un dict, il reçoit la note du deal (score, lettre)."""
    x = ref[ref.base == s].sort_values("set_num")
    if x.empty or x.prix_vente_estime.isna().all():
        return []
    x = x.iloc[0]
    pp = float(x.prix_public) if x.prix_public == x.prix_public else None
    lignes = [f"Prix public Lego {euros(pp)}"] if pp else []
    rc = x.get("retrait_croise")
    rt = pd.to_datetime(rc if rc == rc and rc else x.retrait, errors="coerce")
    if x.statut == "en vente" and rt == rt and rt <= pd.Timestamp.today() + pd.DateOffset(months=4):
        niv = x.get("niveau_retrait")
        niv = f" ({niv} : {x.sources_retrait})" if niv == niv and niv else " (Brickset seul)"
        lignes.insert(0, f"🔚 PRIORITÉ : quitte le catalogue le {rt:%d/%m/%Y}{niv}"
                      + (" · exclusivité Lego, souvent épuisée au Black Friday" if x.get("exclusif") in (True, "True") else ""))
    scen = []  # (nom, prix de revente, jours)
    if x.get("pv_rapide") == x.get("pv_rapide") and x.get("pv_rapide"):
        scen.append(("Revente rapide (set encore au catalogue)", float(x.pv_rapide), int(x.j_rapide)))
        if x.get("pv_garde") == x.get("pv_garde") and x.get("pv_garde"):
            # Même date de retrait que garde_possible (croisée entre sources quand on l'a)
            quand = rt if rt == rt else pd.to_datetime(x.retrait, errors="coerce")
            if garde_possible(x):
                j = int(x.j_garde)
                brickset = pd.to_datetime(x.retrait, errors="coerce")
                if brickset == brickset and quand == quand:
                    j = max(30, j - (brickset - quand).days)
                scen.append((f"Si tu gardes jusqu'au retrait ({quand:%m/%Y})", float(x.pv_garde), j))
            elif quand == quand:
                lignes.append(f"Retrait prévu vers {quand:%m/%Y} : trop loin pour garder, revente rapide seulement")
    else:
        scen.append(("Revente (set retiré du catalogue)", float(x.prix_vente_estime),
                     int(x.delai_jours) if x.get("delai_jours") == x.get("delai_jours") else 90))
    meilleur, calc = None, []
    for nom, pv, j in scen:
        g = net(pv, prix)
        an = (g / prix) * 365 / max(j, 30)
        calc.append((nom, pv, j, g, an))
        # La stratégie retenue est celle qui rapporte le plus en net, pas en rendement annuel :
        # quelques centimes en 1 mois ne valent pas un vrai gain en garde.
        if g / prix >= MARGE_NETTE_MIN and (meilleur is None or g > meilleur[0]):
            meilleur = (g, an, j, nom)
    if meilleur:
        st = meilleur[3].replace("Si tu gardes", "garder").split(" (set")[0]
        lignes.append(f"👉 Stratégie : {st[0].lower() + st[1:]}, argent immobilisé {duree(meilleur[2])}, "
                      f"gain net {euros(meilleur[0])}")
    for nom, pv, j, g, an in sorted(calc, key=lambda c: meilleur is None or c[0] != meilleur[3]):
        if g / prix < MARGE_NETTE_MIN:
            lignes.append(f"{nom} : vente vers {euros(pv)} → gain net {euros(g)}, pas rentable")
        else:
            lignes.append(f"{nom} : vente vers {euros(pv)} dans {duree(j)} → gain net {euros(g)} "
                          f"({g / prix:+.0%}, ≈ {an:.0%} par an)")
    # Prix d'annonce si tu l'achètes : le même que l'appli LEGO HQ (regles.prix_annonce)
    est = regles.estime_revente(x)
    if est:
        pa = regles.prix_annonce(est, prix, pp, x.statut == "en vente")
        lignes.append(f"🏷 Prix d'annonce : {euros(pa['prix'])} (estimation {euros(est)} + {regles.PRIX_DEPART - 1:.0%}"
                      + (f", plafonné sous le prix LEGO" if pa["plafonne"] else "")
                      + f", plancher {euros(pa['plancher'])})"
                      + (f" · sur eBay {euros(regles.prix_ebay(pa['prix']))}" if pa["prix"] else ""))
    eb = prix_ebay().get(s)
    if eb and eb.get("n"):
        ref_pv = est or float(x.prix_vente_estime)
        lignes.append(f"🛒 eBay.fr : dès {euros(eb['bas'])} port compris, médiane {euros(eb['med'])} ({eb['n']} annonce{'s' if eb['n'] > 1 else ''} neuve{'s' if eb['n'] > 1 else ''})"
                      + (" · ⚠ déjà moins cher que notre prix prévu" if eb["n"] >= 3 and eb["bas"] < ref_pv else ""))
    ch = x.get("chaleur_dealabs")
    pop = ("gamme populaire sur Dealabs" if ch >= 150 else "gamme moyennement suivie" if ch >= 70
           else "gamme peu suivie sur Dealabs") if ch == ch and ch is not None else "popularité inconnue"
    nr = risque(x.fiabilite, ch)
    lignes.append(f"Risque : {nr} ({pop}, estimation {x.fiabilite})")
    dm = x.get("demande")
    if dm == dm and dm:
        lignes.append(f"Demande : {dm} ({x.demande_detail})")
    dv = int(x.get("deja_vendus", 0) or 0)
    if note is not None:
        note.update(prix_public=pp, risque=nr, demande=dm if dm == dm and dm else None)
    if meilleur and note is not None:
        note["score"], note["lettre"] = note_deal(meilleur[0], meilleur[1], nr, dm)
        part = gammes_phares().get(str(x.get("gamme", "")).strip().upper())
        if part:
            # Gamme où l'offre est déjà large : ventes en lot, acheteurs qui reviennent
            note["score"] = min(100, round(note["score"] * regles.BONUS_GAMME_PHARE))
            note["lettre"] = "A" if note["score"] >= 60 else "B" if note["score"] >= 35 else "C"
            lignes.append(f"⭐ Ta gamme phare ({part:.0%} de tes ventes sur 12 mois) : offre large, ventes en lot plus faciles")
        note.update(gain=round(meilleur[0], 2), pct=round(meilleur[0] / prix, 3), an=round(meilleur[1], 3),
                    jours=meilleur[2], garde=meilleur[3].startswith("Si tu gardes"))
    if meilleur:
        garde = meilleur[3].startswith("Si tu gardes")
        opportunite = meilleur[0] / prix >= 0.5 and nr == "sûr"
        q = quantite(prix, meilleur[0] / prix, nr, dv, tresor["solde"] if tresor else None, opportunite)
        cap = plafond_garde(prix, opportunite)
        if garde and cap is not None:
            q = min(q, cap)  # place limitée pour stocker longtemps
        if opportunite:  # pas de plafond de place, mais pas plus que ce que le marché absorbe
            q = min(q, MAX_OPPORTUNITE.get(dm if dm == dm else None, 4))
        plafond = q
        if en_stock:
            q = max(0, q - en_stock)  # le stock déjà détenu compte dans l'exposition au set
        diversif = None
        if tresor and q > 0:  # pas tous les œufs dans le même panier
            cap_set = int(PART_MAX_SET * tresor["capital"] // prix) - en_stock
            g = str(x.get("gamme", "")).strip().upper()
            deja = (engage or {}).get(g, 0.0)
            cap_gamme = int(max(0.0, PART_MAX_GAMME * tresor["capital"] - deja) // prix)
            if min(cap_set, cap_gamme) < q:
                q = max(0, min(cap_set, cap_gamme))
                diversif = (f"⚖ Diversification : {euros(deja)} déjà engagés en {g.title()} ces 90 jours"
                            if cap_gamme <= cap_set else f"⚖ Diversification : max {PART_MAX_SET:.0%} du capital sur un même set")
        libre = caisse_utilisable(tresor, garde, opportunite) if tresor else None
        if libre is not None and q > 0:
            q = min(q, int(libre // prix))
        if note is not None:
            note.update(quantite=q, en_stock=en_stock, plafond=plafond, assez=bool(en_stock) and en_stock >= plafond)
        if diversif:
            lignes.append(diversif + " → quantité réduite")
        if en_stock and en_stock >= plafond:
            lignes.append(f"✋ Tu en as déjà {en_stock} en stock, et {plafond} au maximum est conseillé sur ce set : n'en rachète pas")
        elif q <= 0 and diversif:
            lignes.append("Quantité conseillée : 0, ton exposition est déjà suffisante")
        elif q <= 0:
            lignes.append("⛔ Tréso : pas de cash libre pour ce type d'achat sans entamer la réserve"
                          + (" (un achat à garder attend que le cash remonte)" if garde else ""))
        else:
            if opportunite:
                lignes.append("⚡ Vraie opportunité : pas de plafond de stockage, seules la tréso, la demande et la diversification limitent. "
                              "Vérifie la limite par client du marchand (ex. Amazon : 3)")
            lignes.append(f"Quantité conseillée : {q}" + (f" (tu en as déjà revendu {dv})" if dv else "")
                          + (f" (déjà {en_stock} en stock)" if en_stock else "") + f" · {euros(q * prix)} engagés")
        if tresor and q > 0:
            part = q * prix / tresor["solde"]
            lignes.append(f"💰 Tréso {euros(tresor['solde'])} → il resterait {euros(tresor['solde'] - q * prix)}"
                          + (f" · ⚡ opportunité : {part:.0%} de ta tréso" if part > 0.10 else ""))
    dc = decoupe(x, prix)
    if dc:
        lignes.append(dc)
    po = (part_out or {}).get(str(x.get("gamme", "")).strip().upper())
    if po and po[0] >= 3 and po[0] / po[1] >= 0.3:
        lignes.append(f"✂ Gamme souvent revendue en part-out chez toi ({po[0]} sets sur {po[1]}) : "
                      "compare avec la valeur build + minifigs")
    return lignes


def badges(s: str, ref, nt: dict, en_stock: int = 0) -> str:
    """Infos clés en tête d'alerte : retrait, demande, risque, stock."""
    x = ref[ref.base == s].sort_values("set_num")
    out = []
    if len(x):
        x = x.iloc[0]
        rc = x.get("retrait_croise")
        rt = pd.to_datetime(rc if rc == rc and rc else x.retrait, errors="coerce")
        if x.statut == "retiré":
            out.append("🔚 Déjà retiré")
        elif rt == rt:
            mois = (rt.year - pd.Timestamp.today().year) * 12 + rt.month - pd.Timestamp.today().month
            out.append(f"🔚 RETRAIT {rt:%m/%Y} (dans {max(mois, 0)} mois) !" if mois <= 4 else f"🔚 Retrait {rt:%m/%Y}")
        else:
            out.append("🔚 Retrait inconnu")
        if x.get("exclusif") in (True, "True"):
            out.append("🏷 Exclusivité Lego")
    dm = nt.get("demande")
    if dm:
        out.append({"forte": "🔥 Demande forte", "moyenne": "🙂 Demande moyenne"}.get(dm, f"🥶 Demande {dm}"))
    nr = nt.get("risque")
    if nr:
        out.append({"sûr": "🛡 Risque faible", "moyen": "⚠️ Risque moyen"}.get(nr, "❗ Risque élevé"))
    if en_stock:
        out.append(f"📦 {en_stock} en stock")
    return " · ".join(out)


# Seuils du gain net par boîte : ils suivent les deals du moment (quart haut = vert,
# quart bas = rouge), recalculés une fois par jour. Valeurs de repli si trop peu de deals.
GAIN_FORT, GAIN_MOYEN = 15.0, 5.0
SEUILS = CACHE / "seuils_gain.json"


def seuils_gain(ref) -> dict:
    """Quartiles du gain net des deals rentables des 30 derniers jours (90 si moins de 20 deals)."""
    global GAIN_FORT, GAIN_MOYEN
    auj = dt.date.today().isoformat()
    try:
        s = json.loads(SEUILS.read_text())
        if s.get("date") == auj:
            GAIN_FORT, GAIN_MOYEN = s["fort"], s["moyen"]
            return s
    except (OSError, ValueError, KeyError):
        pass
    s = {"date": auj, "fort": GAIN_FORT, "moyen": GAIN_MOYEN, "n": 0, "jours": None}
    for jours in (30, 90):
        depuis = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=jours)).isoformat(timespec="seconds")
        gains = []
        for d in base.lire("deals", f"select=id_set,prix&vu_le=gte.{urllib.parse.quote(depuis)}&limit=5000"):
            if d.get("id_set") and d.get("prix"):
                nt = {}
                try:
                    details(d["id_set"], float(d["prix"]), ref, None, 0, None, nt, None)
                except Exception:
                    continue
                if "gain" in nt:
                    gains.append(nt["gain"])
        if len(gains) >= 20:
            q = pd.Series(gains).quantile([0.25, 0.75])
            s.update(fort=round(float(q[0.75]) * 2) / 2, moyen=round(float(q[0.25]) * 2) / 2, n=len(gains), jours=jours)
            break
    GAIN_FORT, GAIN_MOYEN = s["fort"], s["moyen"]
    try:
        SEUILS.parent.mkdir(parents=True, exist_ok=True)
        SEUILS.write_text(json.dumps(s))
    except OSError:
        pass
    return s


# Offres d'un seul magasin : seulement en Île-de-France, où il peut aller les chercher
IDF = ("75", "77", "78", "91", "92", "93", "94", "95")


def local(titre: str) -> str | None:
    """« … - Châlons-en-Champagne (51) » : offre d'un seul magasin, à signaler en tête."""
    m = re.match(r"([A-ZÉÈÎ][\w'’\- ]+?)\s*\((\d{2}[AB]?|\d{3})\)", re.split(r"\s+-\s+", titre)[-1])
    return f"📍 Un seul magasin : {m.group(1).strip()} ({m.group(2)})" if m else None


def canal(titre: str) -> str | None:
    """Offre en magasin : le rayon tient souvent plus longtemps que le site, à signaler."""
    if re.search(r"\bmagasins?\b|en rayon|drive", titre, re.I) and not local(titre):
        return "🏬 En magasin : même si le site est vide, le rayon peut encore en avoir"
    return None


def pastille(g: float) -> str:
    return "🟩" if g >= GAIN_FORT else "🟨" if g >= GAIN_MOYEN else "🟥"


def ligne_gain(nt: dict) -> str | None:
    """Gain net de la meilleure stratégie, en couleur, pour juger d'un coup d'œil."""
    if "gain" not in nt:
        return None
    g = nt["gain"]
    mot = " · gros potentiel" if g >= GAIN_FORT else " · gain faible" if g < GAIN_MOYEN else ""
    return f"{pastille(g)} GAIN NET {'+' if g > 0 else ''}{euros(g)} par boîte ({nt['pct']:+.0%}) en {duree(nt['jours'])}{mot}"


NOTE_MOTS = {"A": "⭐⭐⭐ Excellente affaire", "B": "⭐⭐ Bonne affaire", "C": "⭐ Affaire correcte"}


def main(essai: bool):
    global MUET
    MUET = github_surveille()
    vus = set(json.loads(VUS.read_text())) if VUS.exists() else set()
    premier = not vus  # premier lancement : on note sans alerter sur l'existant
    # Deals déjà alertés ailleurs (GitHub ou cloud) : la base et la liste publiée
    # chiffrée évitent d'envoyer deux fois le même deal.
    graine = CACHE / "alertes_vus_graine.json"
    if graine.exists():
        vus |= set(json.loads(graine.read_text()))
    vus |= {str(r["id"]) for r in base.lire("deals", "select=id&limit=20000")}
    # Liste complète des deals déjà vus, partagée par tous les passages (le cache
    # de GitHub peut se perdre : le 22/10, 23 anciens deals ont été renvoyés)
    partage = base.doc("fichiers/alertes_vus")
    vus_base = set((partage or {}).get("v") or [])
    vus |= vus_base
    premier = premier and not vus
    ref = charger()
    seuils_gain(ref)
    stock = base.stock_par_set()  # vide si la base n'est pas joignable
    tresor = base.tresorerie()   # None tant que la table est vide
    engage = base.engage_par_gamme()
    part_out = base.part_out_par_gamme()
    journal = []
    for d in deals_recents():
        if d["id"] in vus:
            continue
        vus.add(d["id"])
        promo = detecter(d["titre"])
        if promo:
            # Promo Lego.com (points doublés, set offert) : toujours signalée, avec les sets rentables.
            journal.append({"vu": dt.datetime.now().isoformat(timespec="minutes"), "id": d["id"],
                            "set": promo["cadeau"], "prix": None, "verdict": "promo lego", "prix_max": None,
                            "raison": f"points x{promo['mult_points']}", "titre": d["titre"]})
            if not premier or essai:
                telegram(message_promo(d["titre"], promo, ref, f"Deal à {d['temperature']:.0f}° · {d['lien']}"),
                         True, essai)
            continue
        br = operations.bruit(d["titre"])
        if br:
            # Jeu vidéo, animation en magasin : noté, jamais envoyé
            journal.append({"vu": dt.datetime.now().isoformat(timespec="minutes"), "id": d["id"], "set": None,
                            "prix": d["prix"], "verdict": "non", "prix_max": None, "raison": br, "titre": d["titre"],
                            "lien": d["lien"], "temperature": d["temperature"], "en_stock": 0})
            continue
        if not br and local(d["titre"]) and not local(d["titre"]).endswith(tuple(f"({x})" for x in IDF)):
            br = "magasin unique hors Île-de-France"
            journal.append({"vu": dt.datetime.now().isoformat(timespec="minutes"), "id": d["id"], "set": None,
                            "prix": d["prix"], "verdict": "non", "prix_max": None, "raison": br, "titre": d["titre"],
                            "lien": d["lien"], "temperature": d["temperature"], "en_stock": 0})
            continue
        op = operations.taux(d["titre"], d["prix"])
        if op:
            liste = operations.sets_rentables(op[0], d["titre"], ref, stock, details)
            top = liste[0]["gain"] if liste else None
            v_dec = "achète" if top is not None and top >= GAIN_FORT else "à regarder"
            journal.append({"vu": dt.datetime.now().isoformat(timespec="minutes"), "id": d["id"],
                            "set": None, "prix": d["prix"], "verdict": v_dec,  # pas un set unique : l'app garde le verdict
                            "prix_max": None, "titre": d["titre"], "lien": d["lien"], "temperature": d["temperature"],
                            "raison": f"opération {op[1]} : {len(liste)} sets rentables" + (f", jusqu'à {euros(top)} net" if liste else ""),
                            "en_stock": 0})
            if premier and not essai:
                continue
            corps = [("🟢 OPÉRATION" if v_dec == "achète" else "🟡 OPÉRATION")
                     + (f" · {pastille(top)} jusqu'à +{euros(top)} net par boîte" if liste else ""), d["titre"],
                     *([x for x in (local(d["titre"]), canal(d["titre"])) if x]),
                     f"🧮 {op[1]}" + (f" · gammes : {', '.join(liste[0]['gammes'])}" if liste and liste[0]["gammes"] else ""),
                     f"🛒 Voir le deal : https://www.dealabs.com/visit/threadmain/{d['id']}"]
            if liste:
                corps.append("Sets rentables à ce taux, s'ils font partie de l'opération (prix public moins la remise) :")
                corps += [f"{pastille(o['gain'])} +{euros(o['gain'])} · {o['set']} {o['nom']} · {euros(o['prix'])} "
                          f"(public {euros(o['public'])}) · {duree(o['jours'])}" + (f" · 📦 {o['stock']} en stock" if o["stock"] else "")
                          for o in liste]
            else:
                corps.append("Aucun set du catalogue n'est rentable à ce taux.")
            corps.append(f"Deal à {d['temperature']:.0f}° en ce moment · {d['lien']}")
            if liste:
                telegram("\n".join(corps), v_dec == "achète", essai)
            continue
        s = extraire_set(d["titre"])
        try:  # simple vérification du numéro : une erreur ici ne doit jamais bloquer l'alerte
            s, douteux = corriger_set(d["titre"], s, ref)
        except Exception as e:
            print("corriger_set :", e)
            douteux = None
        cadeau = re.search(r"offerte?s?", d["titre"], re.I)
        if cadeau and s:
            # Offre « set offert dès X € » : on chiffre le cadeau, l'achat se décide au panier.
            x = ref[ref.base == s]
            val = x.prix_vente_estime.iloc[0] if len(x) else None
            seuil = re.search(r"d[èe]s\s*(\d+)\s*€", d["titre"])
            v_dec, pm = "à regarder", None
            v_raison = (f"cadeau {s} revendable environ {val * 0.79:.0f} € nets" if val == val and val else
                        f"cadeau {s} non chiffré") + (f" pour {seuil.group(1)} € d'achat" if seuil else "")
        elif not s:
            v_dec, v_raison, pm = "à regarder", "numéro de set introuvable dans le titre", None
        elif d["prix"] is None:
            v_dec, v_raison, pm = "à regarder", "prix absent du deal", None
        else:
            v = juger_set(s, d["prix"], ref)
            v_dec, v_raison, pm = v.decision, v.raison, v.prix_max
        journal.append({"vu": dt.datetime.now().isoformat(timespec="minutes"), "id": d["id"], "set": s,
                        "prix": d["prix"], "verdict": v_dec, "prix_max": pm, "raison": v_raison,
                        "titre": d["titre"], "lien": d["lien"], "temperature": d["temperature"],
                        "en_stock": stock.get(s, 0) if s else 0})
        if premier and not essai:
            continue
        # Set déjà retiré vendu loin sous son prix public : stock rare, parti en quelques
        # minutes. Il est toujours envoyé (même jugé « non »), sonne, et passe en tête.
        r = ref[ref.base == s] if s and not cadeau else ref.iloc[0:0]
        rare = bool(len(r) and r.statut.iloc[0] == "retiré" and d["prix"]
                    and r.prix_public.iloc[0] == r.prix_public.iloc[0]
                    and d["prix"] <= regles.SEUIL_RARE * float(r.prix_public.iloc[0]))
        if v_dec != "non" or rare:
            tete = "🟢 ACHÈTE" if v_dec == "achète" else "🟡 À regarder"
            corps = [tete, d["titre"]]
            nt = {}
            if d["prix"] and s and not cadeau:
                det = details(s, d["prix"], ref, tresor, stock.get(s, 0), part_out, nt, engage)
                if nt.get("assez"):
                    v_dec, tete = "à regarder", "🟡 À regarder (déjà assez en stock)"
                if nt.get("lettre"):
                    tete += " · " + NOTE_MOTS[nt["lettre"]]
                    if nt["lettre"] == "A" and v_dec == "achète":
                        tete = "🚨 GROSSE AFFAIRE · " + tete
                corps[0] = tete
                lg = ligne_gain(nt)
                if lg:
                    corps.insert(1, lg)
                bd = badges(s, ref, nt, stock.get(s, 0))
                if bd:
                    corps.append(bd)
                corps += [x for x in (local(d["titre"]), canal(d["titre"])) if x]
                if douteux:
                    corps.append(douteux)
                pos = len(corps)
                corps.append(f"Prix du deal {euros(d['prix'])} · achète jusqu'à {euros(pm)}" if pm else v_raison)
                # Retrait, demande et risque sont déjà dans les badges : pas de doublon plus bas
                corps += [l for l in det if not l.startswith(("🔚", "Risque :", "Demande :"))]
            else:
                if douteux:
                    corps.append(douteux)
                pos = len(corps)
                corps.append(v_raison)
            if rare:
                corps[0] = f"⚡ RARE, set retiré à {d['prix'] / float(r.prix_public.iloc[0]) - 1:.0%} · va vite · " + corps[0]
            corps.append(f"Deal à {d['temperature']:.0f}° en ce moment · {d['lien']}")
            # Lien direct vers la page du marchand (redirection Dealabs), pour acheter vite
            corps.insert(pos, f"🛒 Acheter : https://www.dealabs.com/visit/threadmain/{d['id']}")
            # Seules les grosses affaires (note A) et les raretés sonnent ; le reste arrive en silencieux
            gros = v_dec == "achète" and nt.get("lettre") == "A"
            telegram("\n".join(corps), gros or bool(rare), essai)
    if not essai:
        VUS.parent.mkdir(parents=True, exist_ok=True)
        VUS.write_text(json.dumps(sorted(vus)))
        if not essai and vus - vus_base:
            base.doc_ecrire("fichiers/alertes_vus", {"v": sorted(vus)})
        if journal:
            colonnes = ["vu", "id", "set", "prix", "verdict", "prix_max", "raison", "titre"]
            pd.DataFrame(journal)[colonnes].to_csv(JOURNAL, mode="a", header=not JOURNAL.exists(), index=False)
            base.ajouter("deals", [{
                "id": str(j["id"]), "titre": j["titre"], "lien": j.get("lien"), "temperature": j.get("temperature"),
                "id_set": j["set"], "prix": j["prix"], "verdict": j["verdict"], "prix_max": j["prix_max"],
                "raison": j["raison"], "deja_en_stock": j.get("en_stock")} for j in journal], fusion=True)
    resume = f"{len(journal)} nouveaux deals : " + ", ".join(
        f"{n} {k}" for k, n in pd.Series([j['verdict'] for j in journal]).value_counts().items())
    if MUET:
        resume += " (envoyés par GitHub)"
    print(resume)
    return resume


def alarme_panne(erreur: str):
    """Telegram ne doit jamais se taire en silence : après 3 passages ratés d'affilée,
    message d'alarme (au plus un par heure), qui sonne."""
    try:
        der = base.lire("executions", "select=ok&tache=eq.alertes&fin=not.is.null&order=id.desc&limit=3")
        if len(der) < 3 or any(x["ok"] for x in der):
            return
        f = CACHE / "alarme_panne.txt"
        if f.exists() and dt.datetime.now().timestamp() - f.stat().st_mtime < 3600:
            return
        telegram("⚠️ SURVEILLANCE DES DEALS EN PANNE : les 3 derniers passages ont échoué, "
                 "aucune alerte ne part. Va voir Dealabs à la main en attendant la correction.\n"
                 f"Erreur : {erreur[:300]}", True, False)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(erreur)
    except Exception as x:
        print("alarme impossible :", x)


if __name__ == "__main__":
    essai = "--essai" in sys.argv
    id_exec = None if essai else base.debut_execution("alertes")
    try:
        base.fin_execution(id_exec, True, main(essai))
    except Exception as e:
        base.fin_execution(id_exec, False, erreur=repr(e))
        if not essai:
            alarme_panne(repr(e))
        raise
