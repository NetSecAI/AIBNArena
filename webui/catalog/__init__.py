"""What the form can offer, derived from the files that define it."""
from __future__ import annotations

from webui import endpoints

from .architectures import ARCHITECTURES, Architecture, architecture
from .experiments import ExperimentPreset, preset, presets
from .prompts import variants
from .scenarios import Method, Scenario, for_experiment, scenarios

__all__ = [
    "ARCHITECTURES",
    "Architecture",
    "ExperimentPreset",
    "Method",
    "Scenario",
    "architecture",
    "document",
    "for_experiment",
    "preset",
    "presets",
    "scenarios",
    "variants",
]


def document() -> dict[str, object]:
    """The whole catalog, as the browser reads it once on page load."""
    return {
        "endpoints": [item.public() for item in endpoints.load()],
        "architectures": [
            {
                "key": item.key,
                "label": item.label,
                "default_port": item.default_port,
                "default_model": item.default_model,
                "flags": sorted(item.flags),
                "prompt_variants": list(variants(item)),
            }
            for item in ARCHITECTURES
        ],
        "experiments": [
            {
                **item.as_dict(),
                "scenarios": [
                    scenario.as_dict()
                    for scenario in for_experiment(item.domain, item.topology)
                ],
            }
            for item in presets()
        ],
    }
