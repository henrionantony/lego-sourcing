"""Prépare les annonces eBay des derniers sets reçus (stock sûr) dans la base privée.

Pour chaque set : titre (80 caractères max), description, prix eBay (regles.prix_ebay du prix
d'annonce), quantité reçue, port France / UE + Suisse / Royaume-Uni (port.py), photo produit
officielle, EAN, gamme, pièces, année. Écrit le document « ebay/a_publier » ; ebay_annonces.py
(GitHub) les publie et note l'annonce dans « ebay/annonces ».

Rien ne part sans l'accord du vendeur : chaque jour, les nouveaux sets proposés et les changements de
prix des annonces en ligne vont dans « ebay/propositions » (liste à valider ensemble). Seuls les sets
validés (« valides » de ebay/reglages) entrent dans « ebay/a_publier » ; une annonce en ligne garde son
prix tant que le nouveau n'est pas validé.

Usage : python3 ebay_preparer.py                       prépare la file et la liste du jour
        python3 ebay_preparer.py valider <set> …       valide des sets (nouvelle annonce ou nouveau prix)
        python3 ebay_preparer.py liste                 affiche la liste du jour
"""
import glob
import gzip
import html
import json
import math
import re
import sys
from datetime import date
import urllib.parse
from pathlib import Path

import base
import port
import regles

ICI = Path(__file__).resolve().parent
APP = ICI.parent / "stock" / "app"


def brickset() -> dict:
    out = {}
    for f in glob.glob(str(ICI.parent / "data" / "cache" / "brickset_*.json.gz")):
        for s in json.load(gzip.open(f)):
            out[str(s.get("number"))] = s
    return out


def derniers_recus() -> dict[str, int]:
    """Quantité par set des articles neufs scellés reçus et pas encore mis en vente.
    Sans les sets gardés pour le part-out (stratégie LOT/DETAILS, ou figurine / build déjà vendu à part) :
    la photo officielle montre le set entier, ce serait trompeur."""
    q = {}
    entames = {v["article_id"] for v in base.lire("ventes", "select=article_id&produit=in.(FIGURINE,SET%20PART)"
                                                   "&article_id=not.is.null&limit=20000")}
    for a in base.lire("articles", "select=id,id_set,quantite,co_investisseur,strategie&categorie=eq.SET&livre=eq.true"
                                   "&statut=eq." + urllib.parse.quote("à traiter") + "&details_etat=eq.SCELLE&limit=1000"):
        if "LOT" in str(a.get("strategie") or "") or a["id"] in entames:
            continue
        if a.get("id_set") and not a.get("co_investisseur"):
            s = str(a["id_set"])
            q[s] = q.get(s, 0) + int(a.get("quantite") or 1)
    return q


def en_ligne_vinted() -> dict[str, int]:
    """Sets neufs scellés en stock qui ont une annonce Vinted en ligne : stock tenu à jour, donc sûr.
    Un exemplaire chacun (le reste du stock n'est pas toujours recompté)."""
    try:
        annonces = json.loads((APP / "annonces.json").read_text())
    except (OSError, ValueError):
        return {}
    vus = {a.get("n") for a in annonces if a.get("s") == "en ligne" and a.get("n")
           and not re.search(r"complet|occasion|\blot\b|monté|sans boite|sans boîte", a.get("t", ""), re.I)}
    entames = {v["article_id"] for v in base.lire("ventes", "select=article_id&produit=in.(FIGURINE,SET%20PART)"
                                                   "&article_id=not.is.null&limit=20000")}
    associes = {a["id"] for a in base.lire("articles", "select=id&co_investisseur=not.is.null&limit=10000")}
    q = {}
    for a in base.lire("stock", "select=id,id_set,strategie,details_etat,reste&categorie=eq.SET&reste=gt.0&limit=10000"):
        s = str(a.get("id_set") or "").split("-")[0].removesuffix(".0")
        if (s in vus and a["id"] not in associes and "LOT" not in str(a.get("strategie") or "")
                and a["id"] not in entames and str(a.get("details_etat") or "").upper().startswith("SCELL")):
            q[s] = 1
    return q


def description(s, en, fr, b, note="") -> str:
    nom = f"{fr} ({en})" if fr and fr.lower() != en.lower() else en
    pieces, annee, gamme = b.get("pieces"), b.get("year"), b.get("theme")
    lignes = [f"<h2>LEGO {html.escape(gamme or '')} {s} – {html.escape(nom)}</h2>",
              "<p><b>Neuf, boîte scellée</b> (jamais ouverte).</p>" + (f"<p>{'' if note.startswith('⚠️') else '⚠️ '}{html.escape(note)}</p>" if note else "") + "<ul>",
              f"<li>Numéro du set : {s}</li>"]
    if gamme: lignes.append(f"<li>Gamme : {html.escape(gamme)}</li>")
    if pieces and str(pieces) != "0": lignes.append(f"<li>Pièces : {pieces}</li>")
    if annee: lignes.append(f"<li>Année : {annee}</li>")
    # Boîte avec défauts (note) : ne pas promettre une boîte en bon état sous la photo officielle
    lignes += ["</ul><p>Photo officielle du produit : " + ("la boîte de cet exemplaire a les défauts indiqués plus haut.</p>" if note
               else "le set envoyé est neuf et scellé, boîte en bon état.</p>"),
               "<p>📦 Envoi soigné en carton avec calage, en Colissimo suivi depuis la France.</p>",
               "<p>🇪🇺 Envoi aussi vers plusieurs pays d'Europe et le Royaume-Uni (voir les options de livraison).</p>",
               "<p>Vendeur particulier passionné, plus de 500 avis positifs sur d'autres plateformes.</p>"]
    return "".join(lignes)


NOUVELLES_PAR_JOUR = 5
CACHE_IMG = ICI.parent / "data" / "cache" / "brickset_images.json"
PORT_FIG = {"fr": 4.0, "ue": None, "uk": None, "kg": 0.1, "lettre": True, "add": 1.0, "groupe": 1}   # lettre suivie 100 g, +1 € par figurine en plus
PORT_SET_EN_PLUS = 4.0   # même annonce, chaque set en plus : +4 €
_imgs = None


def photos_set(b: dict) -> list:
    """Photo officielle du set puis toutes les photos officielles en plus (Brickset), 24 au plus.
    Jamais les photos des annonces Vinted : sur eBay, on doit avoir l'air d'un autre vendeur."""
    global _imgs
    if _imgs is None:
        try:
            _imgs = json.loads(CACHE_IMG.read_text())
        except (OSError, ValueError):
            _imgs = {}
    sid = str(b.get("setID") or "")
    if sid and sid not in _imgs and int(b.get("additionalImageCount") or 0):
        import os
        import urllib.request
        try:
            u = "https://brickset.com/api/v3.asmx/getAdditionalImages?" + urllib.parse.urlencode(
                {"apiKey": os.environ["BRICKSET_API_KEY"], "setID": sid})
            r = json.load(urllib.request.urlopen(u, timeout=30))
            if r.get("status") == "success":
                _imgs[sid] = [i["imageURL"] for i in r.get("additionalImages", []) if i.get("imageURL")]
                CACHE_IMG.write_text(json.dumps(_imgs))
        except Exception:
            pass
    img = (b.get("image") or {}).get("imageURL")
    return ([img] if img else []) + _imgs.get(sid, [])[:23]


def fiche_figurine(f: dict, noms: dict) -> dict:
    """Annonce d'une figurine du part-out : photos officielles BrickLink (jamais les siennes)."""
    code, nom, s = f["code"], f["nom"], str(f["set"])
    sfr = (noms.get(s) or {}).get("nom") or ""
    gamme = f.get("gamme") or ""
    for t in (f"LEGO {gamme} Minifigure {nom} {code} - {s} - Neuve", f"LEGO Minifigure {nom} {code} - {s} - Neuve",
              f"LEGO Minifig {nom} {code} - Neuve"):
        if len(t.replace("  ", " ")) <= 80:
            break
    t = t.replace("  ", " ")[:80]
    d = (f"<h2>Minifigure LEGO {html.escape(gamme)} – {html.escape(nom)} ({code})</h2>"
         f"<p><b>Neuve</b>, issue du set {s}{' ' + html.escape(sfr) if sfr else ''} acheté neuf et ouvert pour l'occasion : jamais jouée, "
         "jamais exposée.</p><ul>"
         f"<li>Référence BrickLink : {code}</li><li>Set d'origine : {s}</li>"
         "<li>Figurine complète telle qu'elle sort du set, avec ses accessoires d'origine.</li></ul>"
         "<p>Photo officielle de la figurine.</p>"
         "<p>✉️ Envoi en lettre suivie, figurine protégée. Plusieurs figurines : <b>+1 € seulement par figurine en plus</b>, "
         "tout part dans le même envoi.</p>"
         "<p>Vendeur particulier passionné, plus de 500 avis positifs sur d'autres plateformes.</p>")
    return {"titre": t, "description": d, "prix": float(f["prix"]), "qte": int(f.get("qte") or 1), "port": dict(PORT_FIG),
            "images": [f"https://img.bricklink.com/ItemImage/MN/0/{code}.png", f"https://img.bricklink.com/ItemImage/ML/{code}.png"],
            "gamme": gamme, "nom": nom, "code": code, "figurine": True, "set_source": s}


def main():
    ap = json.loads((APP / "a_publier.json").read_text())
    noms = json.loads((APP / "noms_fr.json").read_text())
    bs = brickset()
    # Stock sûr : derniers colis reçus, plus les sets en stock qui ont une annonce Vinted en ligne
    voulus = en_ligne_vinted()
    for s, q in derniers_recus().items():
        voulus[s] = max(q, voulus.get(s, 0))
    reglages = base.doc("ebay/reglages") or {}
    annonces = base.doc("ebay/annonces") or {}
    prix_ebay = base.doc("ebay/prix") or {}
    en_ligne = {s for s, a in annonces.items() if not a.get("retire")}
    retirees = {s for s, a in annonces.items() if a.get("retire")}
    valides = set(reglages.get("valides", [])) | en_ligne
    out, sans, chez_lego, propositions, maj = {}, [], [], [], []
    for s, q in voulus.items():
        v, b = ap.get(s) or {}, bs.get(s) or {}
        if s in retirees or s in reglages.get("exclus", []):
            continue  # retirée par toi (ou par une règle) : on ne la remet pas d'office
        if not v.get("prix") or not b:
            sans.append(s)
            continue
        en, fr = b.get("name") or "", (noms.get(s) or {}).get("nom") or ""
        t = f"LEGO {s} {en}" + (f" / {fr}" if fr and fr.lower() != en.lower() else "") + " - Neuf scellé"
        if len(t) > 80:
            t = f"LEGO {b.get('theme') or ''} {s} {en} - Neuf scellé".replace("  ", " ")
        if len(t) > 80:
            t = f"LEGO {s} {en} - Neuf scellé"[:80]
        p = port.port(s, b.get("pieces"))
        if not p or not p["fr"] or not p["ue"] or p["hors_norme"]:
            sans.append(s)
            continue
        # Encore vendu par LEGO : jamais plus cher que chez LEGO (prix + port France), sinon on attend le retrait
        pp = v.get("pp")
        calcule = regles.prix_ebay(v["prix"])
        # Croisement marché / marge : port compris, pas plus cher que la médiane des annonces eBay
        # (au moins 3), mais jamais sous le plancher de marge (coût × marge brute minimale).
        eb = prix_ebay.get(s) or {}
        plancher = max(float(v.get("plancher") or 0), regles.PRIX_MIN)
        marche = eb["med"] - p["fr"] if (eb.get("n") or 0) >= 3 and eb.get("med") else None
        sous_plancher = bool(marche and marche < plancher)
        if marche:
            # Bien moins cher que tout eBay : on remonte juste sous l'annonce la moins chère
            if eb.get("bas") and calcule < eb["bas"] - p["fr"] - 1:
                calcule = round(eb["bas"] - p["fr"] - 1)
            calcule = float(max(min(calcule, round(marche)), math.ceil(plancher)))
        # Mode vente (26/10 : récupérer du cash en gardant la marge) :
        # le moins cher d'eBay, port compris, dès que le plancher le permet
        # sans baisser de plus de 15 % d'un coup : les offres acceptées seules font le reste
        if reglages.get("vente_rapide") and eb.get("bas"):
            calcule = float(max(min(calcule, round(eb["bas"] - p["fr"] - 1)), round(calcule * 0.85), math.ceil(plancher)))
        if v.get("st") == "en vente" and pp and calcule + p["fr"] > pp:
            if s in en_ligne:
                chez_lego.append(s)
            continue
        q = reglages.get("qte", {}).get(s, q)
        p = {**p, "add": PORT_SET_EN_PLUS, "groupe": 1}   # « groupe » : envoi groupé (remise du compte eBay)
        # Prix validé (à la main) sinon : en ligne = le prix actuel, nouveau = le prix calculé
        fixe = reglages.get("prix", {}).get(s)
        prix = float(fixe or (annonces[s]["prix"] if s in en_ligne else calcule))
        if reglages.get("vente_rapide"):
            prix = min(prix, calcule)   # en mode vente, on ne fait que baisser, jamais sous le plancher
        fiche = {"titre": t, "description": description(s, en, fr, b, reglages.get("notes", {}).get(s, "")), "prix": prix,
                 "qte": q or 1, "port": {k: p[k] for k in ("fr", "ue", "uk", "kg", "add", "groupe")}, "images": photos_set(b),
                 "ean": (b.get("barcode") or {}).get("EAN"), "gamme": b.get("theme"), "pieces": b.get("pieces"),
                 "annee": b.get("year"), "nom": en,
                 "plancher": math.ceil(plancher) if reglages.get("vente_rapide") else None}
        info = {"set": s, "nom": en, "fr": fr, "prix": calcule, "port": p["fr"], "qte": q or 1, "cout": v.get("cout"),
                "estime": v.get("estime"), "st": v.get("st"), "retrait": v.get("retrait"),
                "ebay_bas": eb.get("bas"), "ebay_med": eb.get("med"), "ebay_n": eb.get("n"),
                "net": round(calcule * (1 - regles.PRELEVEMENTS) - float(v.get("cout") or 0), 2), "sous_plancher": sous_plancher}
        if s in valides:
            out[s] = fiche
            if s in en_ligne and not fixe and abs(calcule - prix) >= 1:
                maj.append({**info, "ancien": prix})
        else:  # même en pause (stock pas remis à jour), la liste reste proposée : ton OK vaut accord
            propositions.append(info)
    # Les meilleurs d'abord : retirés (ça monte), puis moins chers que le moins cher d'eBay, puis le plus gros prix
    moins_cher = lambda i: not i["ebay_bas"] or i["prix"] + i["port"] <= i["ebay_bas"]
    propositions.sort(key=lambda i: (i["st"] != "retiré", not moins_cher(i), -i["prix"]))
    # Figurines validées (liste ordonnée « figurines » des réglages) : avant les sets, dans l'ordre choisi
    figs = {}
    for i, f in enumerate(reglages.get("figurines", [])):
        k = f"fig-{f['code']}"
        if k not in retirees and not f.get("vendue"):
            figs[k] = {**fiche_figurine(f, noms), "ordre": i}
    out = {**figs, **out}
    base.doc_ecrire("ebay/a_retirer", sorted(chez_lego + reglages.get("exclus", [])))
    if not base.doc_ecrire("ebay/a_publier", out):
        sys.exit("écriture refusée")
    base.doc_ecrire("ebay/propositions", {"le": date.today().isoformat(), "nouvelles": propositions[:NOUVELLES_PAR_JOUR],
                                         "toutes": propositions, "reserve": len(propositions), "maj": maj})
    print(f"{len(out)} annonces dans la file, {len(propositions)} sets proposables, {len(maj)} changements de prix, "
          f"{len(chez_lego)} à retirer (moins chers chez LEGO), {len(sans)} sans prix ou port")


def valider(sets: list[str]):
    """Ton OK : nouvelle annonce → dans la file ; annonce en ligne → le nouveau prix proposé est retenu."""
    reglages = base.doc("ebay/reglages") or {}
    prop = base.doc("ebay/propositions") or {}
    maj = {i["set"]: i["prix"] for i in prop.get("maj", [])}
    for s in sets:
        if s in maj:
            reglages.setdefault("prix", {})[s] = maj[s]
        elif s not in reglages.setdefault("valides", []):
            reglages["valides"].append(s)
    base.doc_ecrire("ebay/reglages", reglages)
    main()


def liste(n: int = NOUVELLES_PAR_JOUR):
    """Les n prochaines nouveautés (5 par jour) et les changements de prix des annonces en ligne."""
    prop = base.doc("ebay/propositions") or {}
    e = lambda x: f"{x:.0f} €" if x else "?"
    # Marge comptée en brut (prix de vente / coût), comme sa règle des +40 %
    net = lambda i: (f"marge brute +{(i['prix'] / i['cout'] - 1) * 100:.0f} %"
                     + (" (sous +40 %, compensé par l'achat)" if i["prix"] < i["cout"] * regles.MARGE_BRUTE_PLANCHER - 0.5 else "")) \
        if i.get("cout") else "cadeau"
    for k, i in enumerate((prop.get("toutes") or prop.get("nouvelles", []))[:n]):
        print(f"J+{k // NOUVELLES_PAR_JOUR} NOUVEAU {i['set']} {i['nom']} | {e(i['prix'])} + port {e(i['port'])} | coût {e(i['cout'])} | {net(i)} | "
              f"{i['st']} {i.get('retrait') or ''} | eBay dès {e(i['ebay_bas'])}, médiane {e(i.get('ebay_med'))} ({i['ebay_n'] or 0})"
              + (" | ⚠ marché sous ton plancher" if i.get("sous_plancher") else ""))
    for i in prop.get("maj", []):
        print(f"PRIX {i['set']} {i['nom']} | {e(i['ancien'])} → {e(i['prix'])} | coût {e(i['cout'])} | {net(i)} | "
              f"eBay dès {e(i['ebay_bas'])}, médiane {e(i.get('ebay_med'))}" + (" | ⚠ marché sous ton plancher" if i.get("sous_plancher") else ""))
    print(f"réserve : {prop.get('reserve')} sets proposables")


if __name__ == "__main__":
    if sys.argv[1:2] == ["valider"]:
        valider(sys.argv[2:])
    elif sys.argv[1:2] == ["liste"]:
        liste(int(sys.argv[2]) if sys.argv[2:] else NOUVELLES_PAR_JOUR)
    else:
        main()
