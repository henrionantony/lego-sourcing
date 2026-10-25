import datetime as dt
"""Moteur de décision du sourcing : prix maximal d'achat et verdict.

Règles du document de contexte : 21 % de prélèvements sur le prix de vente,
jamais sur la marge ; on raisonne en net.
"""
from dataclasses import dataclass

PRELEVEMENTS = 0.21
# Valeurs par défaut ; les vraies valeurs viennent de ../data/parametres.json
# (privé, publié chiffré).
MARGE_NETTE_MIN = 0.15   # net / prix d'achat
NET_MIN_EUROS = 5.0
# Bande « à regarder » au-dessus du prix maximal, selon la fiabilité de
# l'estimation, calée sur le test historique (détail dans le README privé).
# On privilégie le rappel.
BANDES = {"connu": 0.15, "BrickLink": 0.25, "BrickEconomy": 0.25, "mixte": 0.25, "gamme": 0.35, "global": 0.50}
BANDE_A_REGARDER = BANDES["connu"]


def net(prix_vente: float, prix_achat: float, commission: float = 0.0) -> float:
    return prix_vente * (1 - PRELEVEMENTS) - commission - prix_achat


def prix_max(prix_vente_attendu: float, commission: float = 0.0) -> float:
    """Prix d'achat le plus haut qui laisse la marge et le net minimaux."""
    reste = prix_vente_attendu * (1 - PRELEVEMENTS) - commission
    return max(0.0, min(reste / (1 + MARGE_NETTE_MIN), reste - NET_MIN_EUROS))


@dataclass
class Verdict:
    decision: str          # "achète", "à regarder" ou "non"
    prix_max: float | None
    raison: str


def juger(prix: float, prix_vente_attendu: float | None, commission: float = 0.0,
          fiabilite: str = "connu", pm: float | None = None) -> Verdict:
    """`pm` : prix max déjà calculé (meilleur scénario du référentiel), sinon
    déduit du prix de vente attendu."""
    # Un set non chiffré n'est jamais jeté : on le montre.
    if prix_vente_attendu is None:
        return Verdict("à regarder", None, "non chiffré")
    pm = pm if pm is not None else prix_max(prix_vente_attendu, commission)
    if prix <= pm:
        return Verdict("achète", pm, f"{prix:.2f} € ≤ max {pm:.2f} €")
    if prix <= pm * (1 + BANDES.get(fiabilite, BANDE_A_REGARDER)):
        return Verdict("à regarder", pm, f"{prix:.2f} € proche du max {pm:.2f} € (estimation {fiabilite})")
    return Verdict("non", pm, f"{prix:.2f} € > max {pm:.2f} €")


# On raisonne année par année : on ne garde un set encore au
# catalogue que si son retrait tombe dans les HORIZON_GARDE_MOIS prochains mois.
# Les sets déjà retirés (stratégiques) peuvent attendre leur prise de valeur.
HORIZON_GARDE_MOIS = 6
# La place de stockage est limitée : quantité limitée pour un set qu'on garde,
# selon la taille de la boîte (approchée par le prix). Une vraie opportunité
# n'a pas de plafond de stockage : seules la tréso et la limite du marchand comptent.
QUANTITE_MAX_GARDE = 2


def plafond_garde(prix: float, opportunite: bool) -> int | None:
    if opportunite:
        return None
    return 6 if prix < 30 else QUANTITE_MAX_GARDE if prix < 100 else 1


def garde_possible(x) -> bool:
    import pandas as pd
    rc = x.get("retrait_croise")
    rt = pd.to_datetime(rc if rc == rc and rc else x.get("retrait"), errors="coerce")
    return rt == rt and rt <= pd.Timestamp.today() + pd.DateOffset(months=HORIZON_GARDE_MOIS)


def juger_set(numero, prix: float, referentiel) -> Verdict:
    """Verdict d'un deal à partir du référentiel (DataFrame de referentiel.charger()).
    Un set absent ou sans estimation part en « à regarder »."""
    base = str(numero).strip().split("-")[0]
    lignes = referentiel[referentiel.base == base]
    if lignes.empty:
        return Verdict("à regarder", None, f"set {base} inconnu du référentiel")
    x = lignes.sort_values("set_num").iloc[0]
    if x.get("statut") == "en vente" and x.get("pv_rapide") == x.get("pv_rapide") and x.get("pv_rapide") \
            and not garde_possible(x):
        # Retrait trop lointain : on ne parie pas dessus, seule la revente rapide compte.
        x = x.copy()
        x["prix_max"] = prix_max(float(x.pv_rapide))
    if x.prix_vente_estime != x.prix_vente_estime:  # NaN
        return Verdict("à regarder", None, f"{x['name']} : non chiffré")
    pm = x.get("prix_max")
    v = juger(prix, float(x.prix_vente_estime), fiabilite=x.fiabilite,
              pm=float(pm) if pm == pm and pm is not None else None)
    if v.decision == "achète" and x.get("demande") == "faible":
        # Rentable sur le papier mais difficile à revendre : on ne pousse pas l'achat.
        return Verdict("à regarder", v.prix_max, f"{x['name']} : {v.raison}, mais demande faible")
    return Verdict(v.decision, v.prix_max, f"{x['name']} : {v.raison}")


def risque(fiabilite: str, chaleur) -> str:
    """« sûr », « moyen » ou « risqué » : fiabilité de l'estimation croisée
    avec la popularité de la gamme sur Dealabs (température médiane)."""
    froid = chaleur == chaleur and chaleur is not None and chaleur < 70
    inconnu = not (chaleur == chaleur and chaleur is not None)
    if fiabilite in ("global", "non chiffré"):
        return "risqué" if froid or inconnu else "moyen"
    return "moyen" if froid else "sûr"


# Set encore au catalogue : l'acheteur Vinted compare au prix LEGO. Prix affiché
# + protection acheteur (fixe + % du prix) + port doit rester sous le prix public,
# sinon l'annonce ne part pas.
PROTECTION_FIXE, PROTECTION_PCT, PORT_ACHETEUR = 0.70, 0.05, 4.0


def plafond_catalogue(prix_public) -> float | None:
    """Prix d'annonce maximum (à l'euro inférieur) d'un set encore au catalogue."""
    if not prix_public or prix_public != prix_public:
        return None
    return float(max(0, int((float(prix_public) - PROTECTION_FIXE - PORT_ACHETEUR) / (1 + PROTECTION_PCT))))


# eBay.fr depuis le 1/09/2026, vendeur particulier de l'EEE : aucun frais de vente (commission 0).
# L'acheteur paie à eBay des frais de protection : 0,10 € + 7 % jusqu'à 20 € + 4 % de 20 à 300 €
# + 2 % de 300 à 4 000 € (port non compté). Frais vendeur réglables si le compte passe pro,
# ou si on dépasse 150 annonces par mois (0,35 € l'annonce en plus).
EBAY_FRAIS_PCT, EBAY_FRAIS_FIXE = 0.0, 0.0


def protection_ebay(p: float) -> float:
    return 0.10 + 0.07 * min(p, 20) + 0.04 * max(0, min(p, 300) - 20) + 0.02 * max(0, min(p, 4000) - 300)


def protection_vinted(p: float) -> float:
    return PROTECTION_FIXE + PROTECTION_PCT * p


def prix_ebay(prix_vinted: float) -> float:
    """Prix d'annonce eBay équivalent à un prix Vinted : jamais moins de net pour toi
    (frais vendeur eBay ajoutés), et autant que possible tant que l'acheteur eBay ne paie
    pas plus que l'acheteur Vinted (frais de protection eBay plus bas au-delà de ~14 €)."""
    import math
    net_egal = (prix_vinted + EBAY_FRAIS_FIXE) / (1 - EBAY_FRAIS_PCT)
    cible = prix_vinted + protection_vinted(prix_vinted)       # total payé par l'acheteur Vinted, hors port
    bas, haut = 0.0, cible
    for _ in range(40):                                         # prix eBay dont total acheteur = cible
        m = (bas + haut) / 2
        bas, haut = (m, haut) if m + protection_ebay(m) <= cible else (bas, m)
    return float(max(math.ceil(net_egal), math.floor(bas)))


def estime_revente(r) -> float:
    """Prix de revente attendu d'un set (ligne du référentiel, dict ou pandas) : revente rapide si
    encore au catalogue, sinon l'estimation ; retiré : la valeur après retrait si elle est plus haute."""
    f = lambda k: float(r.get(k)) if r.get(k) not in (None, "") and r.get(k) == r.get(k) else 0.0
    e = f("pv_rapide") or f("prix_vente_estime")
    return max(e, f("pv_garde")) if r.get("statut") == "retiré" else e


def prix_annonce(estime: float, cout: float, prix_public=None, au_catalogue: bool = False, plancher=None) -> dict:
    """LE prix d'annonce d'un set, le même partout (alerte Telegram, appli, liste du lundi).
    Départ = estimation de revente × PRIX_DEPART (marge pour les offres et la baisse de 5 %/30 j),
    jamais sous le plancher (coût × MARGE_BRUTE_PLANCHER) ; encore au catalogue : plafonné
    pour que prix + frais acheteur + port reste sous le prix LEGO, sans passer sous le plancher.
    plancher : celui calculé sur l'achat entier (transaction.py), sinon coût × MARGE_BRUTE_PLANCHER."""
    import math
    arr = lambda p: float(math.ceil(p / 5) * 5) if p >= 30 else float(math.ceil(p))
    plancher = cout * MARGE_BRUTE_PLANCHER if plancher is None else plancher
    depart = estime * PRIX_DEPART
    prix = max(depart, plancher, PRIX_MIN, GWP_DEFAUT if cout == 0 and not estime else 0)
    prix = arr(prix) if prix else None
    cap = plafond_catalogue(prix_public) if au_catalogue else None
    plafonne = bool(prix and cap and prix > cap)
    if plafonne:
        prix = max(cap, float(math.ceil(plancher)))
    return {"prix": prix, "depart": round(depart, 2), "plancher": round(plancher, 2), "cap": cap, "plafonne": plafonne}


# Trésorerie disponible quand la base ne la donne pas (table « tresorerie »).
BUDGET = 1000.0
# Valeurs tirées des ventes réelles : neutres ici, les vraies sont dans le fichier privé.
GWP_DEFAUT = 25.0                 # revente d'un cadeau Lego.com non chiffré
PRIX_MIN = 5.0                    # prix d'annonce minimal (polybags…), vraie valeur dans le fichier privé
MARGE_BRUTE_PLANCHER = 1.30       # prix plancher d'annonce tant que le lot n'est pas remboursé
PRIX_DEPART = 1.15                # prix de départ d'une nouvelle annonce = estimation × ça, puis −5 % / 30 j jusqu'au plancher
TELEGRAM_CHAT_ID = None           # destinataire des alertes (privé)
# Part de la trésorerie qu'un deal peut engager : PART_MAX_DEAL pour un deal
# risqué, plus quand la revente est sûre, et encore plus pour une vraie
# opportunité (marge d'au moins 50 %). Ce n'est pas un plafond de principe :
# une bonne affaire peut prendre une grosse part de la trésorerie.
PART_MAX_DEAL = 0.10
PART_PAR_RISQUE = {"sûr": 0.35, "moyen": 0.20}
BONUS_OPPORTUNITE = 0.15


def part_max(niveau_risque: str, gain_pct: float) -> float:
    return min(0.5, PART_PAR_RISQUE.get(niveau_risque, PART_MAX_DEAL) + BONUS_OPPORTUNITE * (gain_pct >= 0.5))


def quantite(prix: float, gain_pct: float, niveau_risque: str, deja_vendus: int = 0, tresor: float | None = None,
             opportunite: bool = False) -> int:
    """Quantité conseillée : selon la confiance dans la revente, la marge et
    la trésorerie. Base sûr 3 / moyen 2 / risqué 1 ; +1 si la marge dépasse
    50 % ou si le set a déjà été revendu au moins deux fois ; −1 sous 25 %
    de marge ; plafond 5 (aucun pour une vraie opportunité) et part_max de la trésorerie."""
    q = {"sûr": 3, "moyen": 2}.get(niveau_risque, 1)
    q += (gain_pct >= 0.5) + (deja_vendus >= 2) - (gain_pct < 0.25)
    caisse = tresor if tresor and tresor > 0 else BUDGET
    if opportunite:
        q = max(q, 999)  # seule la part de tréso limite
    q = min(q, 999 if opportunite else 5, int(caisse * part_max(niveau_risque, gain_pct) // prix) if prix > 0 else 5)
    return max(q, 1)


# Pilotage de la trésorerie : ne jamais être à sec, toujours pouvoir saisir une
# bonne affaire. Les seuils sont en part du capital (solde de départ + apports).
RESERVE = 0.20               # cash jamais engagé...
RESERVE_OPPORTUNITE = 0.10   # ...sauf pour une vraie opportunité (marge ≥ 50 %, revente sûre)
# Diversification : sur le capital, part max dans un même set (stock compris)
# et dans une même gamme achetée ces 90 derniers jours, pour garder de quoi
# saisir les opportunités suivantes (Black Friday, retraits de fin d'année).
PART_MAX_SET = 0.08
PART_MAX_GAMME = 0.20
# Vraie opportunité : pas de plafond de place, mais un maximum d'exemplaires
# (stock compris) selon la demande ; à ajuster avec ce qu'on apprendra des ventes.
MAX_OPPORTUNITE = {"forte": 8, "moyenne": 6}
SEUIL_GARDE = 0.50           # un achat à garder laisse au moins la moitié du capital propre (hors apport)


def note_deal(gain: float, rendement_an: float, niveau_risque: str, demande) -> tuple[int, str]:
    """Note de 0 à 100 pour classer les deals entre eux, et sa lettre (A, B, C).
    Moitié rotation (rendement net annualisé, plein à 150 %/an), moitié gain net
    par set (plein à 40 €), puis pondérée par le risque et la demande."""
    rot = min(max(rendement_an, 0) / 1.5, 1)
    euros_ = min(max(gain, 0) / 40, 1)
    r = {"sûr": 1.0, "moyen": 0.75, "risqué": 0.45}.get(niveau_risque, 0.6)
    d = {"forte": 1.0, "moyenne": 0.8, "faible": 0.5}.get(str(demande).split()[0].lower() if demande == demande and demande else "", 0.7)
    n = round(100 * (0.5 * rot + 0.5 * euros_) * r * d)
    return n, "A" if n >= 60 else "B" if n >= 35 else "C"


# Gamme phare : une gamme qui fait une grosse part des ventes de l'année se vend
# mieux (vrai choix pour l'acheteur, ventes en lot) : la note est un peu relevée.
PART_GAMME_PHARE = 0.15
BONUS_GAMME_PHARE = 1.15

# Set retiré proposé à −30 % ou plus sous son prix public : alerte « rare » qui sonne.
SEUIL_RARE = 0.70

DATE_APPORT = "2026-11-20"  # semaine du Black Friday


def caisse_utilisable(tresor: dict, garde: bool, opportunite: bool) -> float:
    """Cash qu'un achat peut engager sans entamer la réserve. Un achat à garder
    n'utilise jamais l'apport à rendre et laisse au moins la moitié du capital."""
    capital = tresor.get("capital") or tresor["solde"]
    if garde:
        propre = tresor.get("capital_propre") or capital
        return max(0.0, tresor["solde"] - tresor.get("a_rendre", 0) - SEUIL_GARDE * propre)
    # L'apport à rendre est gardé pour le Black Friday : avant DATE_APPORT, seule
    # une vraie opportunité peut y toucher.
    bloque = 0.0 if opportunite or dt.date.today() >= dt.date.fromisoformat(DATE_APPORT) else tresor.get("a_rendre", 0)
    return max(0.0, tresor["solde"] - bloque - (RESERVE_OPPORTUNITE if opportunite else RESERVE) * (capital - bloque))


def _charger_parametres():
    """Remplace les valeurs par défaut par celles du fichier privé, s'il existe."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent / "data" / "parametres.json"
    if f.exists():
        globals().update({k: v for k, v in json.loads(f.read_text()).items() if k.isupper()})


_charger_parametres()
