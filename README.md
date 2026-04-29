# Emploi

CLI Python personnel pour chercher, scorer et suivre les offres d'emploi.

## Installation dev

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Commandes MVP

```bash
emploi init
emploi offer add --title "Technicien support" --company "Entreprise X" --location "Bonneville"
emploi offer list
emploi offer show 1
emploi offer score 1
emploi offer score --all
emploi offer status 1 interesting
emploi offer reject 1 --reason "Permis obligatoire"
emploi offer archive 1
emploi apply 1
emploi application list
emploi report
```

## Base locale

Par défaut, la base SQLite est créée ici :

```txt
~/.local/share/emploi/emploi.sqlite
```

Pour utiliser une autre base :

```bash
EMPLOI_DB=/tmp/emploi.sqlite emploi init
```

## Principe V0

Le MVP reste volontairement simple : saisie manuelle, scoring transparent, suivi local.
Les imports France Travail, matching CV et automatisations viendront ensuite.
