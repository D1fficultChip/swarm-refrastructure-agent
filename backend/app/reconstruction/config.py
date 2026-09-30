from pathlib import Path

from pydantic import Field

from ..models.domain import DomainModel


class PlannerConfig(DomainModel):
    max_subsets: int = Field(default=4096, ge=1)
    max_options: int = Field(default=32, ge=1)
    max_branches: int = Field(default=512, ge=1)
    max_route_expansions: int = Field(default=25000, ge=1)
    max_grid_cells: int = Field(default=50000, ge=4)
    route_deviation_weight: float = Field(default=0.1, ge=0)

    @classmethod
    def load(cls):
        path = Path(__file__).resolve().parents[3] / "config/phase3.json"
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
