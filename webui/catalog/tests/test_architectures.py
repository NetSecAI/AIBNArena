"""The registry says what each subject's server accepts. These hold it to that."""
import ast
import re
from pathlib import Path

from webui.catalog.architectures import ARCHITECTURES
from webui.catalog.prompts import DEFAULT_VARIANT, variants

ROOT = Path(__file__).resolve().parents[3]
FLAG = re.compile(r"""add_argument\(\s*["'](--[a-z0-9-]+)["']""")
PORT = re.compile(r"""add_argument\(\s*["']--port["'],\s*type=int,\s*default=(\d+)""")


def source(module: str) -> str:
    return (ROOT / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8")


def test_every_flag_the_form_offers_exists_on_its_server():
    for architecture in ARCHITECTURES:
        declared = set(FLAG.findall(source(architecture.module)))
        for flag in architecture.flags:
            assert f"--{flag.replace('_', '-')}" in declared, (
                f"{architecture.key} is offered --{flag}, which its server does not accept")


def test_the_port_and_identity_are_the_ones_the_subject_serves():
    for architecture in ARCHITECTURES:
        server = source(architecture.module)
        assert int(PORT.search(server).group(1)) == architecture.default_port
        agent = source(architecture.module.rsplit(".", 1)[0] + ".agent")
        assert architecture.identity in server or architecture.identity in agent


def test_every_server_names_the_model_it_was_started_with():
    """The readiness check holds the runtime document to the model the interface asked
    for (orchestrator/readiness.py); a server that does not state `configured_model`
    there is refused before its first episode."""
    for architecture in ARCHITECTURES:
        tree = ast.parse(source(architecture.module))
        builds = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and getattr(node.func, "id", None) == "build_a2a_application"]
        assert len(builds) == 1, f"{architecture.key} builds its application {len(builds)} times"
        metadata = next((keyword.value for keyword in builds[0].keywords
                         if keyword.arg == "runtime_metadata"), None)
        assert isinstance(metadata, ast.Dict), f"{architecture.key} sends no runtime metadata"
        keys = {key.value for key in metadata.keys if isinstance(key, ast.Constant)}
        assert "configured_model" in keys, f"{architecture.key} does not state its configured model"


def test_prompt_variants_are_the_files_the_subject_ships_minus_its_roles():
    for architecture in ARCHITECTURES:
        directory = ROOT / architecture.module.rsplit(".", 1)[0].replace(".", "/") / "prompts"
        offered = variants(architecture)
        assert offered[0] == DEFAULT_VARIANT
        assert len(set(offered)) == len(offered)
        for name in offered:
            assert (directory / f"{name}.txt").is_file()
        # `question.txt` is a role prompt: running the repair loop under it would
        # measure something else, so it is never offered as a variant.
        assert "question" not in offered
