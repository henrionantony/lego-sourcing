"""Accès à la base Supabase privée pour les scripts (alertes, collecte...).

Sans SUPABASE_URL / SUPABASE_SERVICE_KEY (par exemple sur GitHub sans ces
secrets), ou si une table n'existe pas encore, tout devient sans effet : les
scripts tournent comme avant.
"""
import re
import datetime as dt
import json
import os
import urllib.request


# Octets reçus de la base par ce processus (07/11 : quota de 5 Go/mois dépassé) ; noté dans
# executions.octets pour suivre la consommation de chaque tâche
OCTETS = 0


def _config():
    url, cle = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not cle:
        return None
    entetes = {"apikey": cle, "Content-Type": "application/json"}
    if cle.startswith("eyJ"):  # ancienne clé service_role (JWT)
        entetes["Authorization"] = f"Bearer {cle}"
    return url.rstrip("/").removesuffix("/rest/v1") + "/rest/v1/", entetes


def _appel(methode: str, chemin: str, corps=None, prefer: str = ""):
    c = _config()
    if not c:
        return None
    url, entetes = c
    if prefer:
        entetes = {**entetes, "Prefer": prefer}
    data = json.dumps(corps, default=str).encode() if corps is not None else None
    req = urllib.request.Request(url + chemin, data=data, headers=entetes, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read()
            global OCTETS
            OCTETS += len(txt) + 500  # + en-têtes
            return json.loads(txt) if txt else []
    except Exception as e:  # base injoignable ou table absente : on n'arrête pas le script
        print(f"base : {methode} {chemin.split('?')[0]} impossible ({e})")
        return None


def lire(table: str, requete: str = "select=*") -> list[dict]:
    return _appel("GET", f"{table}?{requete}") or []


def ajouter(table: str, lignes: list[dict], fusion: bool = False):
    if lignes:
        _appel("POST", table, lignes,
               "resolution=merge-duplicates,return=minimal" if fusion else "return=minimal")


def debut_execution(tache: str):
    """Note le début d'un passage ; renvoie son id (ou None)."""
    r = _appel("POST", "executions", [{"tache": tache}], "return=representation")
    return r[0]["id"] if r else None


def fin_execution(id_exec, ok: bool, resume: str = "", erreur: str = ""):
    if id_exec:
        _appel("PATCH", f"executions?id=eq.{id_exec}",
               {"fin": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": ok, "resume": (resume[:480] + f" · base {OCTETS // 1024} Ko"), "erreur": erreur[:2000] or None})


def stock_par_set() -> dict[str, int]:
    """Exemplaires encore en stock par numéro de set (vue « stock »)."""
    out: dict[str, int] = {}
    for l in lire("stock", "select=id_set,reste&reste=gt.0&id_set=not.is.null&statut=neq.termin%C3%A9&limit=10000"):
        s = str(l["id_set"]).split("-")[0].removesuffix(".0")
        out[s] = out.get(s, 0) + int(l["reste"] or 0)
    return out


def engage_par_gamme(jours: int = 90) -> dict[str, float]:
    """Cash encore immobilisé par gamme dans les achats récents (stock restant,
    au prix d'achat), pour ne pas mettre tous les œufs dans le même panier."""
    import datetime as dt
    depuis = (dt.date.today() - dt.timedelta(days=jours)).isoformat()
    out: dict[str, float] = {}
    for l in lire("stock", f"select=gamme,quantite,prix_achat,reste&reste=gt.0&date_achat=gte.{depuis}&limit=10000"):
        g = str(l["gamme"] or "").strip().upper()
        if g and l["quantite"]:
            out[g] = out.get(g, 0.0) + float(l["prix_achat"] or 0) / int(l["quantite"]) * int(l["reste"] or 0)
    return out


def tresorerie():
    """Trésorerie disponible (somme des mouvements de la table « tresorerie »)
    et montant des apports encore à rendre ; None si la table est vide ou absente."""
    lignes = lire("tresorerie", "select=montant,type,libelle,le,vente_id&limit=100000")
    if not lignes:
        return None
    solde = sum(float(l["montant"]) for l in lignes)
    # Les ventes entrent nettes des 21 % de prélèvements, mais l'argent reste sur les
    # comptes jusqu'à l'échéance URSSAF / impôts : on le compte dans le cash, et
    # « impots » dit combien garder ; un paiement (type urssaf ou impots) le fait sortir.
    from regles import PRELEVEMENTS
    k = PRELEVEMENTS / (1 - PRELEVEMENTS)
    ids = [str(l["vente_id"]) for l in lignes if l["type"] == "vente" and l.get("vente_id")]
    frais = {}
    for i in range(0, len(ids), 200):
        for v in lire("ventes", f"select=id,frais_envoi&frais_envoi=not.is.null&id=in.({','.join(ids[i:i + 200])})") or []:
            frais[str(v["id"])] = float(v["frais_envoi"] or 0)
    preleve = sum((float(l["montant"]) + frais.get(str(l.get("vente_id")), 0.0)) * k
                  for l in lignes if l["type"] == "vente")
    paye = -sum(float(l["montant"]) for l in lignes if l["type"] in ("urssaf", "impots") and str(l.get("le") or "") > "2026-10-31")
    solde = round(solde + preleve, 2)
    import datetime as _dt
    auj = _dt.date.today()
    echeance = min(d for d in (_dt.date(a, m, j) for a in (auj.year, auj.year + 1)
                               for m, j in ((1, 31), (4, 30), (7, 31), (10, 31))) if d >= auj)
    a_rendre = sum(float(l["montant"]) for l in lignes if l["type"] in ("apport", "remboursement"))
    capital = sum(float(l["montant"]) for l in lignes if l["type"] in ("départ", "apport"))
    propre = sum(float(l["montant"]) for l in lignes if l["type"] == "départ")
    # Parts des associés encaissées avec les ventes faites ensemble (type « associe »,
    # libellé « À rendre à X : … », un remboursement « Rendu à X : … » en négatif)
    dus: dict[str, float] = {}
    for l in lignes:
        if l["type"] == "associe":
            m = re.match(r"\s*(?:À rendre|Rendu) à ([^:]+?)\s*:", str(l.get("libelle") or ""))
            nom = m.group(1) if m else "?"
            dus[nom] = round(dus.get(nom, 0.0) + float(l["montant"]), 2)
    return {"solde": solde, "impots": round(max(preleve - paye, 0.0), 2), "impots_le": echeance.isoformat(), "a_rendre": max(a_rendre, 0.0), "capital": capital, "capital_propre": propre,
            "dus": {k: v for k, v in dus.items() if abs(v) >= 0.01}}


def part_out_par_gamme() -> dict[str, tuple[int, int]]:
    """Par gamme : (sets revendus en part-out, sets achetés), d'après la stratégie
    notée sur chaque article."""
    out: dict[str, list[int]] = {}
    for l in lire("articles", "select=gamme,strategie,quantite&categorie=eq.SET&limit=10000"):
        g = str(l.get("gamme") or "").strip().upper()
        n = out.setdefault(g, [0, 0])
        q = int(float(l.get("quantite") or 1))
        n[1] += q
        if "LOT" in str(l.get("strategie") or "").upper():
            n[0] += q
    return {g: (a, b) for g, (a, b) in out.items()}


def modifier(table: str, filtre: str, valeurs: dict):
    """Met à jour les lignes qui passent le filtre PostgREST (ex. « id=eq.3 »)."""
    return _appel("PATCH", f"{table}?{filtre}", valeurs, "return=minimal")


# --- Documents de l'application LEGO HQ (table app_docs, sur GitHub Pages) ---

def docs(prefixe: str):
    """Documents dont le chemin commence par le préfixe (ex. « actions/ ») ;
    None si la table n'existe pas encore."""
    import urllib.parse
    filtre = "chemin=like." + urllib.parse.quote(prefixe + "*", safe="/*")
    # 07/11 (quota) : on ne lit d'abord que chemin + date de mise à jour, et le contenu seulement
    # des documents changés depuis la copie gardée sur le disque (même machine, passages suivants)
    tetes = _appel("GET", "app_docs?select=chemin,maj&limit=10000&" + filtre)
    if tetes is None:
        return None
    out, manquants = [], []
    for t in tetes:
        c = _cache_lire(t["chemin"])
        if c and c.get("maj") == t["maj"]:
            out.append({"chemin": t["chemin"], "data": c["data"], "maj": t["maj"]})
        else:
            manquants.append(t["chemin"])
    for i in range(0, len(manquants), 40):
        lot = manquants[i:i + 40]
        liste = ",".join('"' + c.replace('"', '\\"') + '"' for c in lot)
        r = _appel("GET", "app_docs?select=chemin,data,maj&limit=10000&chemin=in.(" + urllib.parse.quote(liste, safe=",") + ")")
        if r is None:
            return None
        for x in r:
            _cache_ecrire(x["chemin"], x)
            out.append(x)
    return out


def _cache_fichier(chemin: str):
    import hashlib
    from pathlib import Path
    return Path.home() / ".cache" / "lhq_docs" / (hashlib.sha256(chemin.encode()).hexdigest()[:24] + ".json")


def _cache_lire(chemin: str):
    try:
        return json.loads(_cache_fichier(chemin).read_text())
    except (OSError, ValueError):
        return None


def _cache_ecrire(chemin: str, x: dict):
    try:
        f = _cache_fichier(chemin)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"maj": x.get("maj"), "data": x.get("data")}, ensure_ascii=False))
    except OSError:
        pass


def doc(chemin: str):
    r = docs(chemin)
    if r is None:
        return None
    return next((x["data"] for x in r if x["chemin"] == chemin), {})


def doc_ecrire(chemin: str, data) -> bool:
    return _appel("POST", "app_docs", [{"chemin": chemin, "data": data}],
                  "resolution=merge-duplicates,return=minimal") is not None


def json_partage(nom: str, local, defaut):
    """Fichier privé partagé entre les passages (cloud et GitHub) : rangé dans
    app_docs sous « fichiers/<nom> » ; le fichier local n'est qu'une copie, et
    sert seul tant que la table n'existe pas."""
    d = doc("fichiers/" + nom)
    if d is None or d == {}:
        if local.exists():
            v = json.loads(local.read_text())
            if d == {}:
                doc_ecrire("fichiers/" + nom, {"v": v})
            return v
        if d is None:  # base injoignable et pas de copie locale : surtout ne rien écraser
            raise RuntimeError(f"fichier partagé {nom} illisible")
        return defaut
    v = d.get("v", defaut)
    try:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(v, ensure_ascii=False, indent=1))
    except OSError:
        pass
    return v


def json_partage_ecrire(nom: str, local, valeur):
    if not doc_ecrire("fichiers/" + nom, {"v": valeur}):
        raise RuntimeError(f"fichier partagé {nom} non enregistré dans la base")
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps(valeur, ensure_ascii=False, indent=1))


def _stockage(methode: str, chemin: str, contenu: bytes | None = None, type_: str = "application/octet-stream"):
    """Espace de stockage privé « app » de Supabase (fichiers privés des passages)."""
    c = _config()
    if not c:
        return None
    url, entetes = c
    entetes = {**entetes, "Content-Type": type_, "x-upsert": "true"}
    racine = url.removesuffix("/rest/v1/") + "/storage/v1/object/"
    req = urllib.request.Request(racine + ("" if methode == "POST" else "authenticated/") + "app/" + chemin,
                                 data=contenu, headers=entetes, method=methode)
    with urllib.request.urlopen(req, timeout=60) as r:
        b = r.read()
        global OCTETS
        OCTETS += len(b) + 500
        return b


def fichier_prive_lire(chemin: str) -> bytes:
    """Lit un fichier privé (table des prix, réglages) ; lève une erreur s'il manque."""
    r = _stockage("GET", "prive/" + chemin)
    if r is None:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY manquants")
    return r


def fichier_prive_ecrire(chemin: str, contenu: bytes):
    if _stockage("POST", "prive/" + chemin, contenu) is None:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY manquants")
