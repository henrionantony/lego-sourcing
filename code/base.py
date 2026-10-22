"""Accès à la base Supabase privée pour les scripts (alertes, collecte...).

Sans SUPABASE_URL / SUPABASE_SERVICE_KEY (par exemple sur GitHub sans ces
secrets), ou si une table n'existe pas encore, tout devient sans effet : les
scripts tournent comme avant.
"""
import datetime as dt
import json
import os
import urllib.request


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
               {"fin": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": ok, "resume": resume[:500], "erreur": erreur[:2000] or None})


def stock_par_set() -> dict[str, int]:
    """Exemplaires encore en stock par numéro de set (vue « stock »)."""
    out: dict[str, int] = {}
    for l in lire("stock", "select=id_set,reste&reste=gt.0&id_set=not.is.null&limit=10000"):
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
    lignes = lire("tresorerie", "select=montant,type&limit=100000")
    if not lignes:
        return None
    solde = sum(float(l["montant"]) for l in lignes)
    a_rendre = sum(float(l["montant"]) for l in lignes if l["type"] in ("apport", "remboursement"))
    capital = sum(float(l["montant"]) for l in lignes if l["type"] in ("départ", "apport"))
    propre = sum(float(l["montant"]) for l in lignes if l["type"] == "départ")
    return {"solde": solde, "a_rendre": max(a_rendre, 0.0), "capital": capital, "capital_propre": propre}


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
    return _appel("GET", "app_docs?select=chemin,data,maj&limit=10000&chemin=like."
                  + urllib.parse.quote(prefixe + "*", safe="/*"))


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
        return r.read()


def fichier_prive_lire(chemin: str) -> bytes:
    """Lit un fichier privé (table des prix, réglages) ; lève une erreur s'il manque."""
    r = _stockage("GET", "prive/" + chemin)
    if r is None:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY manquants")
    return r


def fichier_prive_ecrire(chemin: str, contenu: bytes):
    if _stockage("POST", "prive/" + chemin, contenu) is None:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY manquants")
