"""Figurines des sets achetés pour la découpe (part-out), rattachées à leur achat.

Chaque set gardé pour la découpe (articles « LOT/DETAILS » encore en stock) montre
dans LEGO HQ (onglet Part-out) la liste de ses figurines, avec leurs codes
BrickLink (relevés sur Brickset, qui reprend les codes BrickLink). « I have them
all » envoie le geste figs_lier : une ligne par figurine dans la table figurines,
rattachée à l'article. À la vente d'une figurine, vendre() coche la plus ancienne
ligne pas encore vendue de ce code : la vente compte pour cet achat.

Données : liste des figurines par set gardée dans la base (fichiers/figs_sets),
état affiché dans l'application (etat/decoupe).

Usage : python3 figs_sets.py              met à jour etat/decoupe
        python3 figs_sets.py figs SET     figurines d'un set
"""
import datetime as dt
import html
import re
import sys
import time
import urllib.request

import base

CACHE = "fichiers/figs_sets"
MAX_NOUVEAUX = 15  # sets relevés sur Brickset par passage, pour rester discret


def _brickset(s: str) -> list[list]:
    req = urllib.request.Request(f"https://brickset.com/minifigs/in-{s}-1", headers={"User-Agent": "Mozilla/5.0"})
    t = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(r"<article class='set'><div class='qty'>(\d+)x</div>.*?"
                         r'<img src="https://img\.bricklink\.com/ItemImage/MN/0/([a-z0-9-]+)\.png" title="([^"]*)"', t, re.S):
        out.append([m.group(2), html.unescape(m.group(3)).split(": ", 1)[-1][:120], int(m.group(1))])
    if not out:  # page sans quantités : une de chaque
        out = [[m.group(1), html.unescape(m.group(2)).split(": ", 1)[-1][:120], 1] for m in
               re.finditer(r'<img src="https://img\.bricklink\.com/ItemImage/MN/0/([a-z0-9-]+)\.png" title="([^"]*)"', t)]
    vus, uniq = set(), []
    for f in out:
        if f[0] not in vus:
            vus.add(f[0])
            uniq.append(f)
    return uniq


def num(id_set) -> str:
    return str(id_set or "").split("-")[0].removesuffix(".0").strip()


def figs(sets: list[str], max_nouveaux: int = MAX_NOUVEAUX) -> dict:
    """{set: [[code BrickLink, nom, qté]]} ; relève sur Brickset les sets pas encore connus."""
    cache = (base.doc(CACHE) or {}).get("v") or {}
    neufs = [s for s in dict.fromkeys(sets) if s and s.isdigit() and s not in cache][:max_nouveaux]
    for i, s in enumerate(neufs):
        try:
            cache[s] = _brickset(s)
        except Exception as e:
            print(s, "Brickset :", type(e).__name__)
            continue
        if i < len(neufs) - 1:
            time.sleep(2)
    if neufs:
        base.doc_ecrire(CACHE, {"v": cache})
    return cache


def lignes(article_id=None) -> list[dict]:
    q = "select=id,article_id,ref,nom,vente_id&limit=20000&order=id.asc"
    return base.lire("figurines", q + (f"&article_id=eq.{int(article_id)}" if article_id else ""))


def etat() -> dict:
    tout = [r for r in base.lire("stock", "select=id,achat_id,id_set,designation,quantite,prix_achat,date_achat,lieu,categorie,strategie,reste"
                                 "&reste=gt.0&categorie=eq.SET&limit=10000") if num(r.get("id_set")).isdigit()]
    # Sets gardés pour la découpe, et sets notés « entier » dont une figurine ou la construction est déjà vendue à part
    entames = {v["article_id"] for v in base.lire("ventes", "select=article_id&produit=in.(FIGURINE,SET%20PART)&article_id=not.is.null&limit=20000")}
    stock = [r for r in tout if "LOT" in str(r.get("strategie") or "") or r["id"] in entames]
    stock.sort(key=lambda r: str(r.get("date_achat") or ""), reverse=True)
    connus = figs([num(r["id_set"]) for r in stock])
    lies = {}
    for f in lignes():
        n = lies.setdefault(f["article_id"], {}).setdefault(f["ref"], [0, 0])
        n[0] += 1
        n[1] += 1 if f.get("vente_id") else 0
    ventes = ventes_figs([r["id"] for r in stock])
    out = []
    for r in stock:
        s = num(r["id_set"])
        ok, non = correspondances(ventes.get(r["id"], []), connus.get(s) or [])
        vendus = {}
        for _, refs in ok:
            for ref in refs:
                vendus[ref] = vendus.get(ref, 0) + 1
        out.append({"id": r["id"], "achat": r["achat_id"], "s": s, "t": str(r.get("designation") or "")[:90],
                    "q": int(r.get("quantite") or 1), "d": str(r.get("date_achat") or "")[:10], "l": str(r.get("lieu") or "")[:30],
                    "figs": connus.get(s), "lies": lies.get(r["id"], {}), "vendus": vendus,
                    "autres": [str(v.get("nom_article") or "")[:90] for v in non]})
    return {"maj": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "sets": out}


def cle(code) -> str:
    """Code BrickLink sans zéros de tête : « NJO087 », « njo0087 » et « NJ0087 » → « njo87 »."""
    m = re.fullmatch(r"([a-z]+?)(o?)0*(\d+)([a-z]?)", re.sub(r"[^a-z0-9]", "", str(code or "").lower()))
    if not m:
        return str(code or "").lower()
    pre = m.group(1) + m.group(2)
    if pre == "nj":  # NJ0932 tapé pour NJO932
        pre = "njo"
    return pre + str(int(m.group(3))) + m.group(4)


def _sans_accent(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")


def correspondances(ventes: list[dict], figs: list) -> tuple[list, list]:
    """Ventes de figurines d'un set découpé → [(vente, [codes de la liste])], et les
    ventes non reconnues. Par code BrickLink dans le titre, sinon par le nom."""
    par_cle = {cle(f[0]): f[0] for f in figs or []}
    noms = [(f[0], re.sub(r"[^a-z0-9 ]", " ", _sans_accent(f[1].split(" - ")[0]))) for f in figs or []]  # nom sans variante
    ok, non = [], []
    for v in ventes:
        txt = f"{v.get('nom_article') or ''} {v.get('id_minifig') or ''}"
        refs = [par_cle[cle(c)] for c in re.findall(r"\b[A-Za-z]{2,5}\d{2,4}[a-z]?\b", txt) if cle(c) in par_cle]
        m = None if refs else re.search(r"FIGURINE\s+(.+?)\s*(?:-|$)", str(v.get("nom_article") or ""), re.I)
        if m:
            # « M. QUIPROQUO (MR. WHATSIT) » : le nom et celui entre parenthèses ; tous ses mots dans le nom Brickset
            for essai in [m.group(1).split("(")[0]] + re.findall(r"\(([^)]*)\)", m.group(1)):
                mots = set(re.sub(r"[^a-z0-9 ]", " ", _sans_accent(essai)).split())
                cand = {r for r, n in noms if mots and mots <= set(n.split())}
                if len(cand) == 1:
                    refs = list(cand)
                    break
        (ok if refs else non).append((v, list(dict.fromkeys(refs))) if refs else v)
    return ok, non


def ventes_figs(article_ids: list) -> dict:
    out = {}
    for i in range(0, len(article_ids), 80):
        lot = ",".join(str(int(a)) for a in article_ids[i:i + 80])
        for v in base.lire("ventes", f"select=id,article_id,id_minifig,nom_article,date_vente&produit=eq.FIGURINE&article_id=in.({lot})&order=date_vente.asc&limit=10000"):
            out.setdefault(v["article_id"], []).append(v)
    return out


def lier(article_id: int, liste: list) -> int:
    """Rattache les figurines à l'article : une ligne par figurine. Les lignes pas
    encore vendues de cet article sont remplacées (on peut refaire la liste)."""
    art = base.lire("articles", f"select=id&id=eq.{int(article_id)}")
    if not art:
        raise ValueError("article inconnu")
    base._appel("DELETE", f"figurines?article_id=eq.{int(article_id)}&vente_id=is.null")
    vendues = {}
    for f in lignes(article_id):
        vendues[f["ref"]] = vendues.get(f["ref"], 0) + 1
    rows = []
    for f in liste:
        ref = re.sub(r"[^a-z0-9-]", "", str(f.get("ref") or "").lower())[:30]
        n = max(0, min(50, int(f.get("n") or 1)))
        if not ref:
            continue
        deja = min(n, vendues.get(ref, 0))
        vendues[ref] = vendues.get(ref, 0) - deja
        rows += [{"article_id": int(article_id), "ref": ref, "nom": str(f.get("nom") or "")[:200], "quantite": 1}] * (n - deja)
    base.ajouter("figurines", rows)
    # Figurines de cet article déjà vendues avant la liste : leur vente est cochée
    pris = {f.get("vente_id") for f in lignes(article_id)}
    ok, _ = correspondances(ventes_figs([article_id]).get(int(article_id), []), [[f["ref"], f.get("nom") or ""] for f in liste])
    for v, refs in ok:
        if v["id"] not in pris:
            for r in refs:
                vendre(v["id"], r, article_id)
    return len(rows)


def vendre(vente_id, ref, article_id=None):
    """Coche la figurine vendue : la plus ancienne pas encore vendue de ce code,
    dans l'article de la vente s'il est connu, sinon dans n'importe quel achat.
    Renvoie l'article d'origine (ou None)."""
    k = cle(ref)
    if not k or not vente_id:
        return None
    pre = re.match(r"[a-z]+", k)
    q = f"select=id,article_id,ref&vente_id=is.null&order=id.asc&limit=2000&ref=like.{pre.group(0) if pre else k}*"
    trouve = lambda rs: next((r for r in rs if cle(r["ref"]) == k), None)
    r = (trouve(base.lire("figurines", q + f"&article_id=eq.{int(article_id)}")) if str(article_id or "").isdigit() else None) \
        or trouve(base.lire("figurines", q))
    if not r:
        return None
    base.modifier("figurines", f"id=eq.{r['id']}", {"vente_id": int(vente_id)})
    return r["article_id"]


def main():
    e = etat()
    if not base.doc_ecrire("etat/decoupe", e):
        raise RuntimeError("écriture de etat/decoupe refusée")
    n = sum(1 for x in e["sets"] if x["figs"])
    print(len(e["sets"]), "sets à découper,", n, "avec leurs figurines")


if __name__ == "__main__":
    if sys.argv[1:2] == ["figs"]:
        for f in figs([sys.argv[2]]).get(sys.argv[2]) or []:
            print(*f)
    else:
        main()
