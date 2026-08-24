from casadata.geo.casablanca import Gazetteer, default_gazetteer, normalize_text


def test_normalize_text():
    assert normalize_text("Maârif,  Casablanca!") == "maarif casablanca"


def test_exact_alias():
    m = default_gazetteer().match("Maârif")
    assert m.slug == "maarif" and m.confidence >= 0.9
    assert m.arrondissement == "Maârif"


def test_contained_alias():
    m = default_gazetteer().match("Bel appartement à Aïn Diab, Casablanca")
    assert m.slug == "ain-diab"
    assert 0.5 < m.confidence < 0.95


def test_misspelling_alias():
    assert default_gazetteer().match("ain chok").slug == "ain-chock"


def test_arabic_alias():
    assert default_gazetteer().match("شقة في المعاريف").slug == "maarif"


def test_city_fallback():
    m = default_gazetteer().match("Quartier inconnu, Casablanca")
    assert m.slug == "casablanca" and m.confidence <= 0.4


def test_no_match():
    assert default_gazetteer().match("Rabat Agdal") is None
    assert default_gazetteer().match(None) is None


def test_longest_alias_wins():
    # 'maarif extension' doit gagner sur 'maarif'
    m = default_gazetteer().match("appartement maarif extension 3e étage")
    assert m.slug == "maarif-extension"


def test_gazetteer_levels_consistent():
    gz = Gazetteer()
    for loc in gz.iter_locations():
        assert loc["level"] in ("city", "prefecture", "arrondissement", "quartier", "micro_quartier")
        if loc["level"] == "micro_quartier" and loc.get("parent"):
            assert loc["parent"] in gz.by_slug
