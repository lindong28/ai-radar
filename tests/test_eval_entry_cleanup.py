"""Current entry defaults and legacy compatibility, without model calls."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_eval_object_datasets import setup_build

from evals._shared.assets import ROOT, load_dataset, validate_layout
from evals._shared.object_datasets import build


@pytest.mark.parametrize("explicit", [None, "aihot-observed-membership", "aihot-prefilter"])
@pytest.mark.parametrize("entry", ["api", "cli"])
def test_builder_current_default_and_explicit_legacy(tmp_path, explicit, entry):
    args = setup_build(tmp_path)
    expected = explicit or "aihot-observed-membership"
    if entry == "api":
        if explicit:
            args["admission_benchmark"] = explicit
        result = build(**args, version="v1", targets=["news-admission"])
    else:
        command = [sys.executable, "scripts/build_eval_datasets.py", "build", "--version", "v1",
                   "--target", "news-admission", "--reference", str(args.pop("references")[0])]
        for key, value in args.items():
            command.extend(["--" + key.replace("_", "-"), str(value)])
        if explicit:
            command.extend(["--admission-benchmark", explicit])
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
    manifest, cases = load_dataset(Path(result["datasets"]["news-admission"]["path"]))
    assert manifest["benchmark"] == expected
    # These feed fixtures lack a reliable historical publication bound: the
    # current contract keeps its positive only in recall-only, unlike +/-12h.
    assert len(cases) == (1 if expected == "aihot-prefilter" else 0)
    assert manifest["counts"]["recall_only"] == 1


def test_prompt_compatibility_link_and_registered_entries():
    current = ROOT / "evals/news-admission/prompts"
    legacy = ROOT / "evals/news-admission/aihot-prefilter/prompts"
    assert legacy.is_symlink() and legacy.resolve() == current
    prompts = list(current.glob("*.json"))
    assert prompts
    for prompt in prompts:
        assert (legacy / prompt.name).read_bytes() == prompt.read_bytes()
        assert set(json.loads(prompt.read_text())) == {"system", "user_template"}
    validate_layout()
