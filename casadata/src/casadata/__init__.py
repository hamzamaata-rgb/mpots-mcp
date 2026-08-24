"""casadata — plateforme de données du marché immobilier résidentiel de Casablanca.

Pensée en observations horodatées multi-sources, pas en annonces :
chaque passage de collecte ajoute des observations (append-only), les prix
ne sont jamais écrasés, chaque donnée est traçable (source, run, brut,
confiance, flags qualité).
"""
__version__ = "0.1.0"

from .db import connect, connect_memory  # noqa: F401
