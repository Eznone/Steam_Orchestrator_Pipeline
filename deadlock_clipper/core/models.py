from dataclasses import asdict, dataclass, field


@dataclass
class Kill:
    tick: int
    attacker_hero_id: int
    attacker_hero_name: str
    victim_hero_id: int
    victim_hero_name: str
    assister_hero_ids: list[int] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict, heroes: dict) -> "Kill":
        return cls(
            tick=row["tick"],
            attacker_hero_id=row["attacker_hero_id"],
            attacker_hero_name=heroes.get(row["attacker_hero_id"], "Unknown"),
            victim_hero_id=row["victim_hero_id"],
            victim_hero_name=heroes.get(row["victim_hero_id"], "Unknown"),
            assister_hero_ids=row["assister_hero_ids"],
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Player:
    steam_id: str
    player_name: str
    hero_id: int
    hero_name: str
    team_num: int

    @classmethod
    def from_row(cls, row: dict, heroes: dict) -> "Player":
        return cls(
            steam_id=str(row["steam_id"]),
            player_name=row["player_name"],
            hero_id=row["hero_id"],
            hero_name=heroes.get(row["hero_id"], "Unknown"),
            team_num=row["team_num"],
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ObjectiveEvent:
    tick: int
    objective_type: str
    team_num: int
    lane: int

    @classmethod
    def from_row(cls, row: dict) -> "ObjectiveEvent":
        return cls(
            tick=row["tick"],
            objective_type=row["objective_type"],
            team_num=row["team_num"],
            lane=row["lane"],
        )

    def to_dict(self) -> dict:
        return asdict(self)
