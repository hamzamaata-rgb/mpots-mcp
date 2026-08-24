"""Déduplication : rapprocher les annonces qui décrivent le même bien réel.

Principes :
- blocking d'abord (type de transaction + quartier + nb pièces ± surface
  arrondie) pour ne comparer que des paires plausibles ;
- score pondéré multi-signaux [0,1] ; JAMAIS de fusion destructive :
  * score >= 0.80 -> lien 'auto'
  * 0.55 <= score < 0.80 -> lien 'candidate' (revue manuelle possible)
  * description identique (hash) -> lien 'auto' quel que soit le reste ;
- les groupes liés sont réunis en 'property' par union-find ;
- relancer la dédup est idempotent (liens recalculés, biens conservés).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

AUTO_THRESHOLD = 0.80
CANDIDATE_THRESHOLD = 0.55


@dataclass
class ListingFacts:
    listing_id: int
    source_id: int
    transaction_type: str
    property_type: str | None
    location_id: int | None
    lat: float | None
    lon: float | None
    price: float | None
    surface: float | None
    rooms: int | None
    bedrooms: int | None
    floor: int | None
    description_hash: str | None
    description: str | None
    agency: str | None


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {t for t in re.findall(r"[\wÀ-ÿ؀-ۿ]{4,}", text.lower())}


def _geo_distance_m(a: ListingFacts, b: ListingFacts) -> float | None:
    if None in (a.lat, a.lon, b.lat, b.lon):
        return None
    dlat = (a.lat - b.lat) * 111_320
    dlon = (a.lon - b.lon) * 111_320 * math.cos(math.radians((a.lat + b.lat) / 2))
    return math.hypot(dlat, dlon)


def score_pair(a: ListingFacts, b: ListingFacts) -> float:
    """Score de similarité [0,1]. Conçu prudent : des caractéristiques proches
    ne suffisent pas, il faut des signaux convergents."""
    if a.transaction_type != b.transaction_type:
        return 0.0
    if a.property_type and b.property_type and a.property_type != b.property_type:
        return 0.0
    if a.description_hash and a.description_hash == b.description_hash:
        return 1.0

    score = 0.0
    if a.surface and b.surface:
        rel = abs(a.surface - b.surface) / max(a.surface, b.surface)
        if rel <= 0.02:
            score += 0.25
        elif rel <= 0.06:
            score += 0.15
        else:
            return 0.0  # surfaces incompatibles: pas le même bien
    if a.price and b.price:
        rel = abs(a.price - b.price) / max(a.price, b.price)
        if rel <= 0.05:
            score += 0.15
        elif rel <= 0.12:
            score += 0.08
    if a.bedrooms is not None and a.bedrooms == b.bedrooms:
        score += 0.10
    if a.floor is not None and a.floor == b.floor:
        score += 0.10
    dist = _geo_distance_m(a, b)
    if dist is not None:
        if dist <= 150:
            score += 0.20
        elif dist <= 400:
            score += 0.10
        elif dist > 1500:
            score -= 0.20
    ta, tb = _tokens(a.description), _tokens(b.description)
    if ta and tb:
        jacc = len(ta & tb) / len(ta | tb)
        if jacc >= 0.6:
            score += 0.25
        elif jacc >= 0.35:
            score += 0.12
    if a.agency and b.agency and a.agency.strip().lower() == b.agency.strip().lower():
        score += 0.10
    return max(0.0, min(1.0, score))


def _load_facts(conn) -> list[ListingFacts]:
    rows = conn.execute(
        """
        SELECT l.listing_id, l.source_id, l.transaction_type, l.property_type,
               l.location_id, l.lat, l.lon,
               o.price, o.surface_m2, o.rooms, o.bedrooms, o.floor,
               o.description_hash, o.description, s.agency_name
        FROM listing l
        JOIN latest_observation o ON o.listing_id = l.listing_id
        LEFT JOIN seller s ON s.seller_id = l.seller_id
        """
    ).fetchall()
    return [ListingFacts(*r) for r in rows]


def _blocking_key(f: ListingFacts) -> tuple:
    surface_bucket = int(f.surface // 10) if f.surface else -1
    return (f.transaction_type, f.location_id, f.rooms, surface_bucket)


class _UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def run_dedup(conn) -> dict:
    """Recalcule les liens annonce<->bien. Retourne des compteurs."""
    facts = _load_facts(conn)
    blocks: dict[tuple, list[ListingFacts]] = defaultdict(list)
    for f in facts:
        key = _blocking_key(f)
        blocks[key].append(f)
        # les surfaces à cheval sur deux buckets doivent aussi se rencontrer
        if f.surface:
            neighbour = (key[0], key[1], key[2], key[3] + 1)
            blocks[neighbour].append(f)

    pairs: dict[tuple[int, int], float] = {}
    for members in blocks.values():
        if len(members) < 2 or len(members) > 400:  # garde-fou blocs dégénérés
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if a.listing_id == b.listing_id:
                    continue
                key = (min(a.listing_id, b.listing_id), max(a.listing_id, b.listing_id))
                if key in pairs:
                    continue
                s = score_pair(a, b)
                if s >= CANDIDATE_THRESHOLD:
                    pairs[key] = s

    uf = _UnionFind()
    for f in facts:
        uf.find(f.listing_id)
    for (a, b), s in pairs.items():
        if s >= AUTO_THRESHOLD:
            uf.union(a, b)

    # reconstruit les biens : un property par composante (même singleton)
    conn.execute("DELETE FROM property_link")
    conn.execute("DELETE FROM property")
    by_root: dict[int, list[ListingFacts]] = defaultdict(list)
    facts_by_id = {f.listing_id: f for f in facts}
    for f in facts:
        by_root[uf.find(f.listing_id)].append(f)

    n_props = n_auto = n_candidates = 0
    for root, members in by_root.items():
        ref = facts_by_id[root]
        surfaces = [m.surface for m in members if m.surface]
        prop_id = conn.execute(
            """INSERT INTO property (property_type, location_id, surface_m2, rooms,
                   bedrooms, floor, lat, lon)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING property_id""",
            [ref.property_type, ref.location_id,
             sum(surfaces) / len(surfaces) if surfaces else None,
             ref.rooms, ref.bedrooms, ref.floor, ref.lat, ref.lon],
        ).fetchone()[0]
        n_props += 1
        for m in members:
            key = (min(root, m.listing_id), max(root, m.listing_id))
            score = 1.0 if m.listing_id == root else pairs.get(key, AUTO_THRESHOLD)
            conn.execute(
                """INSERT INTO property_link (property_id, listing_id, score, method, status)
                   VALUES (?, ?, ?, 'blocking_v1', 'auto')""",
                [prop_id, m.listing_id, score],
            )
            n_auto += 1

    # paires 'candidate' (0.55-0.80) : tracées pour revue, sans fusion
    for (a, b), s in pairs.items():
        if s < AUTO_THRESHOLD and uf.find(a) != uf.find(b):
            prop_row = conn.execute(
                """SELECT property_id FROM property_link WHERE listing_id = ?""", [a]
            ).fetchone()
            if prop_row:
                conn.execute(
                    """INSERT OR IGNORE INTO property_link
                       (property_id, listing_id, score, method, status)
                       VALUES (?, ?, ?, 'blocking_v1', 'candidate')""",
                    [prop_row[0], b, s],
                )
                n_candidates += 1

    return {"listings": len(facts), "properties": n_props,
            "auto_links": n_auto, "candidate_links": n_candidates,
            "scored_pairs": len(pairs)}
