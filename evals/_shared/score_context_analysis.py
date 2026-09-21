"""Paired source ablations and one fixed source-held-out conditional-weight test."""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from pathlib import Path

from airadar.scorer.five import FIVE_WEIGHTS, five_score

from . import assets
from .metrics import score
from .score_weights import solve, source_rows

# Agent-curated channel roles from frozen source names and URLs, not score labels.
# An organization channel can repost: these are NOT per-article firsthand labels.
OFFICIAL = frozenset("apple_ml claude_blog claude_youtube databricks google_ai google_developers "
    "mistral_news nvidia openai_blog openrouter_announcements x_alibaba_cloud x_anthropicai "
    "x_antlingagi x_geminiapp x_googleai x_lumalabsai x_openbmb x_openrouter x_perplexity_ai "
    "x_replit x_siliconflowai x_suno".split())
OTHER = frozenset("buzzing_hn bytebytego elsewhere hf_daily_papers ithome marktechpost techcrunch_ai "
    "the_decoder the_verge_ai x__akhaliq x_aisafetymemes x_alexandr_wang x_aravsrinivas "
    "x_artificialanlys x_ayi_ainotes x_bcherny x_cb_doge x_clementdelangue x_dexhorthy "
    "x_dongxi_nlp x_elonmusk x_emollick x_emostaque x_gdb x_hongming731 x_jxnlco x_karminski3 "
    "x_kimmonismus x_lifesinger x_milichab x_mustafasuleyman x_natolambert x_omarsar0 "
    "x_petermccrory x_rohanpaul_ai x_sama x_semianalysis x_testingcatalog x_thexpin x_thom_wolf "
    "x_thsottiaux x_trq212 x_yuchenj_uw x_zho_zho_zho".split())


def source_role(raw: dict) -> str:
    sid = raw["source_id"]
    return "organization-official" if sid in OFFICIAL else (
        "individual-media-aggregator" if sid in OTHER else "unknown")


def train_case(case: dict) -> bool:
    # Fixed before examining results. Entire source stays on one side.
    key = "score-context-source-split-20260921:" + case["input"]["source_id"]
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 4 != 0


def measurement(cases: list[dict], values: list[int]) -> dict:
    return score("O2", cases, [{"case_id": c["case_id"], "status": "ok", "output": {"score": y}}
                              for c, y in zip(cases, values, strict=True)])


def paired(base: Path, candidate: Path, *, source_root: Path) -> dict:
    _, _, cases, base_dims, bh = source_rows(base, root=source_root)
    _, _, other_cases, new_dims, ch = source_rows(candidate, root=assets.ROOT)
    if cases != other_cases:
        raise ValueError("paired comparison requires identical cases, inputs and gold")
    out = {"baseline_sha256": bh, "candidate_sha256": ch, "groups": {}}
    for role in ("all", "organization-official", "individual-media-aggregator", "unknown"):
        indices = [i for i, c in enumerate(cases) if role == "all" or source_role(c["input"]) == role]
        if not indices:
            continue
        selected = [cases[i] for i in indices]
        a, b = ([five_score(ds[i]) for i in indices] for ds in (base_dims, new_dims))
        out["groups"][role] = {"n": len(indices), "baseline": measurement(selected, a),
            "candidate": measurement(selected, b), "improved": sum(abs(x-c["reference"]["score"]) > abs(y-c["reference"]["score"])
                for c, x, y in zip(selected, a, b, strict=True)),
            "authority_mean_delta": sum(new_dims[i]["authority"]-base_dims[i]["authority"] for i in indices)/len(indices)}
    return out


def conditional(run: Path, *, source_root: Path) -> dict:
    _, metadata, cases, dims, hashes = source_rows(run, root=source_root)
    if metadata["split"] != "dev":
        raise ValueError("fit only dev")
    train = [i for i, c in enumerate(cases) if train_case(c)]
    held = [i for i, c in enumerate(cases) if not train_case(c)]
    if not train or not held:
        raise ValueError("source split lacks one side")
    global_fit = solve([dims[i] for i in train], [cases[i]["reference"]["score"] for i in train])
    groups = {}
    for role in ("organization-official", "individual-media-aggregator"):
        ti = [i for i in train if source_role(cases[i]["input"]) == role]
        hi = [i for i in held if source_role(cases[i]["input"]) == role]
        if ti and hi:
            groups[role] = solve([dims[i] for i in ti], [cases[i]["reference"]["score"] for i in ti])
    views = {}
    for name, indices in (("training", train), ("source-heldout", held)):
        selected = [cases[i] for i in indices]
        shared = global_fit["weights_percent"]
        views[name] = {"n": len(indices), "sources": len({c["input"]["source_id"] for c in selected}),
            "role_counts": dict(Counter(source_role(c["input"]) for c in selected)),
            "case_ids": [c["case_id"] for c in selected],
            "original": measurement(selected, [five_score(dims[i], FIVE_WEIGHTS) for i in indices]),
            "global": measurement(selected, [five_score(dims[i], shared) for i in indices]),
            "conditional": measurement(selected, [five_score(dims[i], groups.get(source_role(cases[i]["input"]), global_fit)["weights_percent"]) for i in indices])}
    return {"source_run": str(run), "source_sha256": hashes, "global": global_fit, "branches": groups,
            "views": views, "new_model_calls": 0, "independence": "internal reused dev; not fresh blind evaluation"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-run", type=Path, required=True)
    p.add_argument("--candidate-run", type=Path, required=True)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError("do not overwrite a frozen analysis")
    result = {"source_ablation": paired(a.baseline_run, a.candidate_run, source_root=a.source_root),
              "A11_conditional": conditional(a.baseline_run, source_root=a.source_root),
              "S1_conditional": conditional(a.candidate_run, source_root=assets.ROOT),
              "role_mapping": {"organization-official": sorted(OFFICIAL), "individual-media-aggregator": sorted(OTHER)},
              "analysis_sha256": assets.file_digest(Path(__file__))}
    assets.write_json(a.output, result)
    print(a.output)


if __name__ == "__main__":
    main()
