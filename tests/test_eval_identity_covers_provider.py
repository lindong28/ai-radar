"""The eval identity record must distinguish providers that carry their own prompt.

Written after ISSUE-FIT-34, not ahead of it. `_PROMPT_FILES` is a fixed map, so a variant
enricher living in another module records the stock file's digest: four A/B arms with very
different classifier prompts wrote byte-identical `prompt_sha256` AND `rendered_inputs_sha256`.
The first reading of that was "the arms are identical, this comparison is void" -- the opposite
misreading, "these are comparable", is the one with no downstream that catches it.

This is the same defect the `_STAGE_BEHAVIOUR_FILES` comment in run.py already describes for the
tag vocabulary. It recurred because the fix there enumerated files rather than following what
actually ran.
"""

from __future__ import annotations

import sys
import types

from airadar.eval.aihot_fit.run import _provider_module_sha256, stage_identity


class _StockEnricher:
    model_id = "deepseek-v4-pro"


def _provider_in_throwaway_module(tmp_path, name: str, body: str):
    """A provider class defined in a file on disk, so its module has a real __file__.

    The module name must be unique per provider: `_provider_module_sha256` resolves the file
    through `sys.modules[type(obj).__module__]`, so reusing one name makes the second provider
    overwrite the first and both digests collapse to the same file -- which looks exactly like
    the defect under test passing.
    """
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(body, str(path), "exec"), module.__dict__)  # noqa: S102
    sys.modules[name] = module
    return module.VariantEnricher()


def test_identity_separates_two_providers_that_share_the_prompt_module(tmp_path) -> None:
    body_a = 'class VariantEnricher:\n    model_id = "deepseek-v4-pro"\n    PROMPT = "classify into five"\n'
    body_b = 'class VariantEnricher:\n    model_id = "deepseek-v4-pro"\n    PROMPT = "stage one then fall back"\n'
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    prov_a = _provider_in_throwaway_module(tmp_path / "a", "variant_enricher_a", body_a)
    prov_b = _provider_in_throwaway_module(tmp_path / "b", "variant_enricher_b", body_b)

    id_a = stage_identity({"enrich": prov_a})["enrich"]
    id_b = stage_identity({"enrich": prov_b})["enrich"]

    # The pre-existing fields cannot tell these apart -- that is ISSUE-FIT-34, asserted so the
    # test fails loudly if someone "fixes" it by changing what prompt_sha256 means instead.
    assert id_a["prompt_sha256"] == id_b["prompt_sha256"]
    assert id_a["rendered_inputs_sha256"] == id_b["rendered_inputs_sha256"]

    # The field added for it must.
    assert id_a["provider_module_sha256"] != id_b["provider_module_sha256"]
    assert id_a["provider_module_sha256"] is not None


def test_provider_module_sha256_is_none_rather_than_raising_without_a_file() -> None:
    """A provider defined in an exec'd namespace has no __file__; identity must degrade, not die."""
    assert _provider_module_sha256(_StockEnricher()) is not None
    namespace: dict[str, object] = {}
    exec("class Ephemeral:\n    model_id = 'x'\n", namespace)  # noqa: S102
    assert _provider_module_sha256(namespace["Ephemeral"]()) is None
