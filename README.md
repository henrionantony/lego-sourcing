# lego-sourcing

Outil de sourcing Lego : repère les bons deals (Dealabs pour commencer) et prévient sur Telegram.

- `code/` : moteur de décision et alertes (voir `code/README.md`).
- `data/prix.csv.enc`, `data/parametres.json.enc` : table des prix et réglages, chiffrés.
- `.github/workflows/alertes.yml` : vérification de Dealabs toutes les 2 minutes environ.

Secret nécessaire : `TELEGRAM_BOT_TOKEN` (il sert aussi à déchiffrer la table des prix).
