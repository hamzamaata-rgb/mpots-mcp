from datetime import datetime, timezone

from casadata.collect import parsing
from casadata.collect.portal import MUBAWAB, parse_listing_page

HTML = """
<html><head>
<title>Appartement à vendre à Maârif</title>
<meta property="og:title" content="Bel appartement 95 m² à Maarif" />
<meta property="og:description" content="Appartement de 95 m² avec 2 chambres, salle de bain, 3ème étage, ascenseur et parking. Prix: 1 350 000 DH" />
<script type="application/ld+json">
{"@type": "Product", "name": "Appartement Maarif",
 "offers": {"@type": "Offer", "price": "1350000", "priceCurrency": "MAD"},
 "geo": {"latitude": "33.5850", "longitude": "-7.6320"}}
</script>
</head><body>
Appartement 95 m² — 3 pièces, 2 chambres, 1 salle de bain, 3ème étage.
Ascenseur, parking, cuisine équipée. 1 350 000 DH
</body></html>
"""


def test_jsonld_extraction():
    blocks = parsing.extract_jsonld(HTML)
    assert parsing.jsonld_offer_price(blocks) == 1_350_000
    lat, lon = parsing.jsonld_geo(blocks)
    assert abs(lat - 33.585) < 1e-6 and abs(lon + 7.632) < 1e-6


def test_price_regex():
    assert parsing.extract_price_mad("Prix : 1 350 000 DH") == 1_350_000
    assert parsing.extract_price_mad("1.250.000 MAD") == 1_250_000
    assert parsing.extract_price_mad("7 500 Dhs / mois") == 7_500
    assert parsing.extract_price_mad("aucun prix ici") is None


def test_feature_extraction():
    f = parsing.extract_features("Appartement 95 m², 3 pièces, 2 chambres, "
                                 "1 salle de bain, 3ème étage avec ascenseur et piscine")
    assert f["surface_m2"] == 95 and f["rooms"] == 3 and f["bedrooms"] == 2
    assert f["bathrooms"] == 1 and f["floor"] == 3
    assert f["attrs"]["elevator"] and f["attrs"]["pool"]


def test_full_listing_page_parse():
    url = "https://www.mubawab.ma/fr/a/7654321/bel-appartement-maarif"
    rec = parse_listing_page(MUBAWAB, url, HTML, "sale",
                             datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert rec.external_id == "7654321"
    assert rec.price == 1_350_000
    assert rec.surface_m2 == 95
    assert rec.bedrooms == 2
    assert rec.lat and rec.lon
    assert rec.attrs.get("elevator") and rec.attrs.get("parking")
    assert "Maarif" in (rec.raw_location or rec.title)
