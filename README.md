# lego-sourcing

Outil de sourcing Lego : repère les bons deals (Dealabs pour commencer) et prévient sur Telegram.

- `code/` : moteur de décision et alertes (voir `code/README.md`).
- Aucune donnée dans ce dépôt : table des prix, réglages et stock sont dans l'espace privé de Supabase.
- `.github/workflows/alertes.yml` : vérification de Dealabs toutes les 30 secondes environ.
- `.github/workflows/mails.yml` : mails Leboncoin de la boîte dédiée (lecture seule) → onglet Platforms.
- `docs/hq/` : l'application (code seul ; les données arrivent après connexion).

Secrets : `TELEGRAM_BOT_TOKEN`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GMAIL_ADRESSE`, `GMAIL_MOT_DE_PASSE`.
