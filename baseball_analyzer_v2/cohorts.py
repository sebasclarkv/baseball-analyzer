"""Build a reproducible MLB hitting cohort from the season before the holdout."""
from __future__ import annotations

from random import Random

from baseball_analyzer_v2.sources import MLB, public_json


def select_cohort(season: int, size: int = 40, seed: int = 42) -> dict:
    if not 30 <= size <= 50:
        raise ValueError("La cohorte admite entre 30 y 50 bateadores.")
    payload, _ = public_json(f"{MLB}/api/v1/stats", {
        "stats": "season", "group": "hitting", "sportIds": 1, "season": season,
        "gameType": "R", "playerPool": "ALL", "limit": 1000, "hydrate": "person",
    }, ttl=86400)
    blocks = payload.get("stats", [])
    if len(blocks) != 1 or not blocks[0].get("splits"):
        raise ValueError("MLB no devolvió una temporada de bateo para seleccionar la cohorte.")
    splits = blocks[0]["splits"]
    if int(blocks[0].get("totalSplits", 0)) > len(splits):
        raise ValueError("La lista de bateadores está paginada y la selección estaría incompleta.")

    groups = {"regular_volume": [], "debut_season": [], "low_pa": []}
    seen = set()
    for split in splits:
        person = split.get("player", {})
        debut = person.get("mlbDebutDate", "")
        if not person.get("id") or not debut or person.get("primaryPosition", {}).get("type") == "Pitcher":
            continue
        player_id = int(person["id"])
        if player_id in seen:
            raise ValueError("MLB devolvió más de un total de temporada para el mismo bateador.")
        seen.add(player_id)
        appearances = int(split.get("stat", {}).get("plateAppearances", 0))
        debut_year = int(debut[:4])
        if debut_year == season and appearances >= 50:
            group = "debut_season"
        elif debut_year < season and appearances >= 500:
            group = "regular_volume"
        elif debut_year < season and 50 <= appearances < 200:
            group = "low_pa"
        else:
            continue
        groups[group].append({"id": player_id, "name": person.get("fullName", str(player_id)),
                              "selection_plate_appearances": appearances,
                              "debut_year": debut_year, "group": group})

    rng = Random(seed)
    players = []
    per_group, remainder = divmod(size, len(groups))
    for index, group in enumerate(groups.values()):
        sample_size = per_group + (index < remainder)
        if len(group) < sample_size:
            raise ValueError("La temporada seleccionada no tiene suficientes jugadores en uno de los grupos.")
        players.extend(rng.sample(sorted(group, key=lambda player: player["id"]), sample_size))
    return {"schema_version": 1, "source": "MLB Stats API / regular-season batting totals",
            "selection_season": season, "holdout_season": season + 1, "seed": seed,
            "population_by_group": {key: len(group) for key, group in groups.items()},
            "selection_rules": {"regular_volume": "500+ PA, debut anterior",
                                "debut_season": "50+ PA, primera temporada MLB",
                                "low_pa": "50 a 199 PA, debut anterior"},
            "players": sorted(players, key=lambda player: player["id"])}
