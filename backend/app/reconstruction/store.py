"""Copy-on-write session bundle: publish state, history and audit in one swap."""
from dataclasses import dataclass, field

from ..models.api import ScenarioSnapshot
from ..models.phase2 import Phase2Result
from ..models.phase4 import ReconstructionResult


@dataclass(frozen=True)
class SessionRecord:
    snapshot: ScenarioSnapshot
    reports: dict[str, Phase2Result] = field(default_factory=dict)
    # proposal ID -> (canonical content hash, reconstruction ID)
    commits: dict[str, tuple[str, str]] = field(default_factory=dict)
    reconstructions: dict[str, ReconstructionResult] = field(default_factory=dict)
