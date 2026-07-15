from __future__ import annotations

from h2lab_tga_app.domain.models import ExperimentRef, RunArtifact, RunConfig
from h2lab_tga_app.infra.state_store import JsonStateStore


class RunService:
    def __init__(self, orchestrator, state_store: JsonStateStore) -> None:
        self.orchestrator = orchestrator
        self.state_store = state_store

    def run(self, experiments: list[ExperimentRef], cfg: RunConfig) -> RunArtifact:
        artifact = self.orchestrator.run_pipeline(experiments, cfg)
        self.state_store.save_recent_run(
            artifact.run_id,
            {
                "reference_temp_c": cfg.reference_temp_c,
                "compute_theory": cfg.compute_theory,
                "save_plots": cfg.save_plots,
            },
        )
        return artifact
