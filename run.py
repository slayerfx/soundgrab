"""Lanceur pour un double-clic ou `python run.py`.

La logique vit dans `soundgrab.cli`, qui sert aussi de point d'entree a la
commande `soundgrab` installee par pip.
"""
from soundgrab.cli import main

if __name__ == "__main__":
    main()
