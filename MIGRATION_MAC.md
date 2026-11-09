# Migration Windows → Mac

> À supprimer une fois la migration terminée.

## Constat (2026-11-09)

- **Tout est sur GitHub.** Le PC Windows ne fait tourner aucun bot : le bot tourne dans GitHub Actions
  et continue pendant la migration.
- Repo local à jour avec `origin/main`, sans branche, stash ni fichier hors git utile
  (seulement des caches `__pycache__` et `.pytest_cache`).
- Secrets Telegram : stockés dans les secrets GitHub, pas sur le PC. Rien à copier.
- ⚠️ Le remote `origin` du PC embarque un **token GitHub (PAT) en clair** dans son URL. Ne pas recopier
  cette config sur le Mac, et **révoquer ce token** (voir l'étape 5).
- Mémoire Claude locale (2 notes) : leur contenu utile est repris dans `CLAUDE.md`, versionné dans le repo.

## Étapes sur le Mac

### 1. Outils

```bash
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install git gh python@3.11
```

### 2. Identité git et authentification GitHub

```bash
git config --global user.name "Ethan Baudrand"
git config --global user.email "ethan.baudrand@gmail.com"
gh auth login        # GitHub.com → HTTPS → navigateur
gh auth setup-git    # git utilise gh pour s'authentifier (plus de PAT dans l'URL)
```

### 3. Cloner et installer

```bash
mkdir -p ~/Projects && cd ~/Projects
gh repo clone ethanbdrd/sol-trader
cd sol-trader
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

### 4. Vérifier

```bash
python -m pytest -q
python sol_analyzer.py
git pull --rebase && git push --dry-run
gh run list --limit 5
```

### 5. Nettoyage côté GitHub

- https://github.com/settings/tokens : révoquer le token `ghp_cpyM…` utilisé par le PC Windows.
- Supprimer ce fichier.

### 6. Claude Code sur le Mac

Ouvrir le dossier `~/Projects/sol-trader` dans Claude Code. `CLAUDE.md` est chargé automatiquement.
