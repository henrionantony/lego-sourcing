"""Opérations magasin (« -50 % sur le 2e », « sélection via cagnotte », « -30 % sur une
sélection ») et bruit (jeux vidéo, animations en magasin) dans les titres Dealabs.

Une opération vaut pour beaucoup de sets : on chiffre le taux de remise, puis on liste
les sets du catalogue qui deviennent rentables à ce taux (prix public × (1 − taux)).
"""
import datetime as dt
import re
import unicodedata

BRUIT = re.compile(
    r"\b(ps[45]|playstation|xbox|switch|nintendo|steam|instant gaming|d[ée]mat[ée]rialis[ée]|sur pc|jeu vid[ée]o)\b"
    r"|construisez|repartez avec|atelier|animation",
    re.I)


def bruit(titre: str) -> str | None:
    """Raison d'écarter un deal qui n'est pas un set à revendre, sinon None."""
    m = BRUIT.search(titre)
    if not m:
        return None
    mot = m.group(0).lower()
    if any(k in mot for k in ("construisez", "repartez", "atelier", "animation")):
        return "animation en magasin, pas un set à revendre"
    return "jeu vidéo LEGO, pas un set"


def _nombre(x: str) -> float:
    return float(x.replace(",", "."))


def taux(titre: str, prix: float | None) -> tuple[float, str] | None:
    """(taux de remise par set, explication) si le titre décrit une opération, sinon None."""
    t = titre.lower()
    m = re.search(r"(\d{2})\s*%[^()]*?(?:sur|au)\s+(?:le|la)?\s*(?:2|deuxi|second)", t)
    if m:
        p = int(m.group(1)) / 100
        return p / 2, f"-{m.group(1)} % sur le 2e set, soit -{p * 50:.0f} % par set en les prenant par deux"
    operation = re.search(r"s[ée]lection|sur tout|tous les|gamme", t)
    if not operation:
        return None
    m = re.search(r"(\d{2})\s*%", t)
    if m:
        p = int(m.group(1)) / 100
        return p, f"-{m.group(1)} % sur une sélection"
    c = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:€|eur)?\s*(?:sur la carte|cagnott|de fid[ée]lit)", t)
    if c and prix:
        cag = _nombre(c.group(1))
        p = cag / (prix + cag)
        return p, f"cagnotte de {p:.0%} du prix (d'après l'exemple du deal)"
    return None


def _sans_accent(x) -> str:
    return unicodedata.normalize("NFKD", str(x).lower()).encode("ascii", "ignore").decode()


def sets_rentables(t: float, titre: str, ref, stock: dict, details, n: int = 8) -> list[dict]:
    """Sets au catalogue rentables à ce taux, les plus gros gains d'abord.
    Limité aux gammes citées dans le titre quand il en cite ; les exclusivités Lego
    seulement pour une opération chez Lego."""
    v = ref[(ref.statut == "en vente") & (ref.categorie_bs == "Normal") & (ref.prix_public >= 10)
            & ref.prix_max.notna() & (ref.year >= dt.date.today().year - 3)].drop_duplicates("base")
    if "(lego)" not in titre.lower():
        v = v[~v.exclusif.isin([True, "True"])]
    # Gammes citées, hors exemple (« Ex : LEGO Star Wars … » n'est qu'un exemple de l'opération)
    tn = _sans_accent(re.split(r"\bex\b\s*:?", titre, flags=re.I)[0])
    gammes = {g for g in v.theme_rb.dropna().unique()
              if len(g) >= 4 and re.search(r"\b" + re.escape(_sans_accent(g).rstrip("s")) + r"s?\b", tn)}
    if gammes:
        v = v[v.theme_rb.isin(gammes)]
    v = v.assign(prix=(v.prix_public * (1 - t)).round(2))
    v = v[v.prix <= v.prix_max]
    v = v.assign(marge=(v.prix_max - v.prix) / v.prix).sort_values("marge", ascending=False).head(4 * n)
    out = []
    for _, x in v.iterrows():
        nt = {}
        details(x.base, float(x.prix), ref, None, stock.get(x.base, 0), None, nt, None)
        if "gain" in nt and not nt.get("assez"):
            out.append({"set": x.base, "nom": x["name"], "prix": float(x.prix), "public": float(x.prix_public),
                        "gain": nt["gain"], "pct": nt["pct"], "jours": nt["jours"], "stock": stock.get(x.base, 0),
                        "gammes": sorted(gammes)})
    return sorted(out, key=lambda o: -o["gain"])[:n]
