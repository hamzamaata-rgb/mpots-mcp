#!/usr/bin/env bash
# Amorce complete de la collecte, sur une machine au reseau ouvert.
#   ./lancer.sh            # calibration (--probe), n'ecrit rien en base
#   ./lancer.sh collecte   # collecte reelle + dedoublonnage + couverture
set -euo pipefail
cd "$(dirname "$0")"

PY=./venv/bin/python
if [ ! -x "$PY" ]; then
    echo "> creation de l'environnement virtuel"
    python3 -m venv venv
    ./venv/bin/pip install -q --upgrade pip
fi
echo "> dependances"
./venv/bin/pip install -q -r requirements.txt

echo "> base et referentiel"
$PY db.py --init

if [ "${1:-probe}" = "probe" ]; then
    echo "> calibration du parser (aucune ecriture en base)"
    if $PY collect.py --probe; then
        echo
        echo "Calibration reussie. Verifier le HTML sauvegarde dans samples/,"
        echo "puis lancer la collecte reelle :  ./lancer.sh collecte"
    else
        echo
        echo "Calibration echouee. Deux causes possibles :"
        echo "  - le reseau ne laisse pas passer avito.ma ;"
        echo "  - les URLs de recherche ou les selecteurs ont change."
        echo "Le HTML recupere, s'il y en a un, est dans samples/ : me l'envoyer suffit"
        echo "pour recalibrer le parser sans acces au site."
        exit 1
    fi
else
    echo "> collecte (5 pages maximum, 3 a 5 s entre requetes)"
    $PY collect.py --pages 5
    echo "> dedoublonnage"
    $PY dedup.py
    echo "> couverture"
    $PY analyse.py
    echo
    echo "Rappel : la couverture par cellule se construit sur plusieurs semaines."
    echo "Une premiere passe ne publiera rien, c'est attendu."
fi
