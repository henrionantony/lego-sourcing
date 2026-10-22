#!/bin/sh
# Publie le code sur GitHub, pour les alertes rapides. Les données privées (table
# des prix, réglages) vont dans l'espace privé de Supabase : rien d'autre que du
# code dans le dépôt public.
# Usage : sh publier.sh [dossier du clone]  (défaut /home/user/lego-sourcing)
set -e
ICI=$(cd "$(dirname "$0")" && pwd)
DEPOT=${1:-/home/user/lego-sourcing}
[ -d "$DEPOT/.git" ] || git clone --depth 1 https://github.com/henrionantony/lego-sourcing "$DEPOT"
mkdir -p "$DEPOT/code"
# Le README de code/ reste privé : il contient les résultats sur les ventes.
cp "$ICI"/*.py "$ICI"/*.sh "$DEPOT/code/"
# Page LEGO Scan (reconnaissance des minifigs), servie par GitHub Pages depuis docs/
mkdir -p "$DEPOT/docs" && cp "$ICI/../scan/index.html" "$DEPOT/docs/index.html"
# Application LEGO HQ : code seul (connexion), les données restent dans Supabase
python3 "$ICI/../app-source/build_pages.py" >/dev/null
mkdir -p "$DEPOT/docs/hq" && cp "$ICI/../app-source/pages/page.html" "$DEPOT/docs/hq/index.html"
# Données de l'appli (stock, annonces, photos…) : dans la base privée, envoyées seulement si elles ont changé
(cd "$ICI" && python3 - "$ICI/../app-source/pages" <<'P3'
import hashlib, json, sys
from pathlib import Path
import base
d = Path(sys.argv[1])
brut = (d / "static_page.json").read_bytes()
h = hashlib.sha256(brut).hexdigest()
f = d / "static_envoye.sha"
if not f.exists() or f.read_text() != h:
    if not base.doc_ecrire("static/page", json.loads(brut)):
        sys.exit("envoi des données de l'appli refusé")
    f.write_text(h)
    print("données de l'appli envoyées")
P3
)
# Table des prix (sous-ensemble utile aux alertes) et réglages : envoyés dans
# l'espace privé de Supabase, jamais dans le dépôt public (même chiffrés).
TMP=$(mktemp -d)
python3 - "$ICI/../data/referentiel.csv" "$TMP/prix.csv" <<'P2'
import sys, pandas as pd
r = pd.read_csv(sys.argv[1], dtype={"base": str}, low_memory=False)
# Toutes les colonnes : la table reste privée, et une colonne oubliée a déjà cassé les alertes (21/10, 22/10)
r[r.prix_vente_estime.notna()].to_csv(sys.argv[2], index=False)
P2
touch -r "$ICI/../data/referentiel.csv" "$TMP/prix.csv"
(cd "$ICI" && python3 prive.py envoyer "$TMP/prix.csv" "$ICI/../data/parametres.json")
rm -rf "$TMP"
(cd "$ICI" && python3 lbc_mails.py index >/dev/null)
git -C "$DEPOT" rm -q --ignore-unmatch data/prix.csv.enc data/parametres.json.enc data/vus.json.enc
cd "$DEPOT"
# Garde-fou (rien de confidentiel dans le dépôt public) :
# refuse de publier si un fichier lisible contient un prénom, des montants en
# euros ou des chiffres de ventes, ou si une donnée non chiffrée traîne.
if grep -rnIiE "antony|henrion|@gmail|[0-9]{8,10}:[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]{20,}|[a-z0-9]{20}\.supabase\.co|chat_id.*[0-9]{6,}|CHAT_ID: *\"?[0-9]|[0-9][0-9 ]*[.,]?[0-9]* ?€ (nets|net|de gain)|chiffre d.affaires|achats_ventes\.xlsx" \
     --include=*.py --include=*.md --include=*.yml --include=*.sh . | grep -v "^./code/publier.sh" | grep -v "henrionantony/lego-sourcing"; then
  echo "PUBLICATION REFUSÉE : contenu possiblement confidentiel ci-dessus" >&2; exit 1
fi
if git ls-files --others --cached --exclude-standard | grep -E "\.(csv|xlsx|json|enc)$"; then
  echo "PUBLICATION REFUSÉE : fichier de données non chiffré" >&2; exit 1
fi
git add -A
git diff --cached --quiet && { echo "rien de nouveau"; exit 0; }
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -qm "Mise à jour du code et de la table des prix

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BuEodcswx2eKut8ZvEDzHx"
git push -q -u origin HEAD:main
echo "publié"
