from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

RULESET_REV: str = "r1"
RULESET_REV_V2: str = "r2"
PINNED_RULESET_DATE: str = "2026-05-13"
# Scoring carries its own date because its behaviour changed on its own schedule: the prompt
# gained a `significance` dimension on 2026-09-06 (ADR-20260906-7c31) while prefilter and enrich
# v1 were untouched. Sharing one constant made rows from either side of that change claim the
# same version -- indistinguishable afterwards -- and, worse, unreachable: the scoring runner
# skips items that already have a row at the current version, so a change that leaves the
# version alone can never reach the archive at all.
PINNED_SCORE_RULESET_DATE: str = "2026-09-06"
# Enrich v2 needs the same treatment for the same reason, and did not get it on
# 2026-09-06: it kept sharing PINNED_RULESET_DATE with prefilter, which is pinned to
# May. Its prompt then changed four times between 2026-09-02 and 2026-09-07 while the
# stamp never moved, so `runner_v2`'s NOT EXISTS(... ruleset_version=?) skipped every
# already-enriched item and the change never reached the archive. Measured on
# 2026-09-08: only 1.8% of the pool's enrich rows came from the prompt then in
# production, 83.2% from a May one. Bumping this alone would also invalidate every
# prefilter row, which is why enrich gets its own constant rather than a shared bump.
#
# It is a date only for readability. A date cannot carry the bump on its own: this
# project changes the enrich prompt more than once a day (three generations landed on
# 2026-09-08 alone), and a same-day second edit leaves the date identical — the
# original bug, silently restored. So the stamp also carries a digest of everything
# that decides what the model is asked, and the date is just a human-readable prefix.
PINNED_ENRICH_RULESET_DATE: str = "2026-09-08"
# Rolling this back is not symmetric: rows already written at the new stamp stay, and
# every consumer reads the latest enrich row by MAX(id), so the new prompt's output
# keeps serving. Meanwhile `runner_v2` would consider those items done under the old
# stamp and never touch them again — a mixed archive the runner cannot heal. Reverting
# means re-running enrich over the affected window, not just reverting this file.


def git_short_hash() -> str:
    return "nogit"


def current_version() -> str:
    """Prefilter's stamp. Moves on its own when the criterion moves.

    PINNED_RULESET_DATE is only a human-readable prefix now; bumping it by hand is not
    required and costs a redundant full-window recompute.
    """

    date = PINNED_RULESET_DATE or datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{date}.{RULESET_REV}.{prefilter_inputs_digest()}"


class _DigestProbeItem:
    """Fixed stand-in so the digest can render the prompt without touching the database.

    Dunders must raise: jinja probes `__html__` when autoescape is on, and returning a
    string for it makes the render raise `TypeError: 'str' object is not callable`
    *inside* `current_version_v2()` — which `run_enrich` calls first, so the whole
    enrich stage would die pointing at jinja rather than at this class.
    """

    def __getattr__(self, name: str) -> str:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return f"<{name}>"


_DIGEST_PROBE_ITEM = _DigestProbeItem()


# Runtime switches that change what gets stored without touching a line of code. The
# rendered prompt cannot see them, and `.env` already carries one of this family.
_ENRICH_RUNTIME_ENV = (
    "AI_RADAR_ENRICHER",
    "AI_RADAR_ARK_ENRICH_MODEL",
    "AI_RADAR_DEEPSEEK_ENRICH_MODEL",
    "AI_RADAR_ENRICH_TEMPERATURE",
)


def enrich_inputs_digest() -> str:
    """Short digest of what the enrich stage would store, given the current inputs.

    Deliberately wider than the system prompt: the user template carries the length
    windows, the tag vocabulary lives in the normalizer, and the category list lives in
    classification.py. Hashing only the system prompt was measured on 2026-09-08 to miss
    all three — a full replacement of the user template left the digest byte-identical.

    It hashes the **rendered** prompt rather than the module's bytes, for two reasons
    both measured the same day. Bytes made the digest move on a comment or a whitespace
    edit, and a spurious move is not free: it re-enriches the whole window (~3,900 calls,
    ~18 hours of the scheduled job's budget) and re-labels roughly a third of the site.
    Bytes were also read at call time while the prompt constants are bound at import
    time, so an edit landing mid-process produced a stamp describing a prompt that
    process was not using — the exact misattribution this digest exists to prevent.
    Rendering covers `render_enrich_prompt` itself, so prompt-assembly logic is still in.

    The three runtime switches go in separately: they decide who answers and how, which
    the rendered text cannot show.
    """
    from .enrich.prompts_v2 import render_enrich_prompt

    rendered = render_enrich_prompt(_DIGEST_PROBE_ITEM)  # type: ignore[arg-type]
    digest = hashlib.sha256()
    digest.update(rendered["system"].encode("utf-8"))
    digest.update(rendered["user"].encode("utf-8"))
    for name in _ENRICH_RUNTIME_ENV:
        digest.update(f"{name}={os.environ.get(name, '')}\u0000".encode())
    return digest.hexdigest()[:8]


def prefilter_inputs_digest() -> str:
    """Digest of the rendered prefilter prompt, so its stamp moves when the prompt does.

    Same defect and same fix as the enrich stamp below. Prefilter's PINNED_RULESET_DATE
    sat at 2026-05-13 while the criterion changed, and `current_version()` is what the
    candidate query's `NOT EXISTS (... ruleset_version=?)` compares — so an edited
    criterion was unreachable for every already-judged item, and worse, new rows carried
    the same stamp as rows produced by a different criterion, making the two
    indistinguishable after the fact.

    Digest the *rendered* prompt, not the module bytes: editing a comment or reflowing
    a line would otherwise trigger a redundant full-window recompute.
    """

    from .prefilter.prompts import render_prefilter_prompt

    rendered = render_prefilter_prompt(_DIGEST_PROBE_ITEM)  # type: ignore[arg-type]
    digest = hashlib.sha256()
    digest.update(rendered["system"].encode("utf-8"))
    digest.update(rendered["user"].encode("utf-8"))
    return digest.hexdigest()[:8]


def score_inputs_digest() -> str:
    """Digest of the rendered scoring prompt, so its stamp moves when the prompt does.

    Third instance of the same defect; the first two are documented above. `runner.py`'s
    candidate query carries `NOT EXISTS (... scored.ruleset_version=?)`, so a scoring
    prompt edit that leaves the stamp alone can never reach an already-scored item, and
    the rows it does write are indistinguishable from rows produced by the older prompt.

    Measured 2026-09-09 before this change: scoring's 46,601 rows at `2026-09-06.r1` all
    came from a single prompt generation, so unlike enrich this had not yet gone wrong --
    the 2026-09-06 bump was done by hand and was correct. What made it worth fixing
    anyway is that a date cannot carry the bump: this project edits prompts more than
    once a day, and a same-day second edit leaves the date identical. That is the enrich
    argument verbatim, and scoring was simply the one stage that never got it.
    """

    from .scorer.prompts import render_scoring_prompt

    rendered = render_scoring_prompt(_DIGEST_PROBE_ITEM)  # type: ignore[arg-type]
    digest = hashlib.sha256()
    digest.update(rendered["system"].encode("utf-8"))
    digest.update(rendered["user"].encode("utf-8"))
    return digest.hexdigest()[:8]


def current_version_v2() -> str:
    """r2 ruleset stamp for the content-v2 enrich pipeline (runner_v2).

    The stamp moves on its own: `enrich_inputs_digest()` derives from the prompt, so a
    prompt edit makes every already-enriched item a candidate again with nobody having
    to remember. PINNED_ENRICH_RULESET_DATE is only a human-readable prefix; bumping it
    by hand is not required and costs a redundant full-window re-enrich.

    Draining the resulting backlog through a wider `--since` needs the pipeline lock:
    `run.sh` does not take the flock `pipeline.sh` holds, so a manual backfill run
    alongside a scheduled round makes that round's `db.migrate()` fail with
    "database is locked" and takes its whole fetch stage down (measured 2026-09-08).

    The .r2 revision suffix keeps v2 rows isolated from v1 (.r1) — no eval/
    content_contract dependency, per the content-v2 integration decision.
    """
    date = PINNED_ENRICH_RULESET_DATE or datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{date}.{RULESET_REV_V2}.{enrich_inputs_digest()}"


def current_score_version() -> str:
    """Scoring's own stamp. The digest moves it; the date is a human-readable prefix.

    A move makes every already-scored item a candidate again, so the next run re-scores whatever
    falls inside its --since window and a wider window backfills the rest. Note what that does
    NOT do: `pipeline.sh` scores `--since 24h`, so rows whose item fell out of that window are
    never revisited by the scheduled job and need an explicit wider backfill.
    """
    date = PINNED_SCORE_RULESET_DATE or datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{date}.{RULESET_REV}.{score_inputs_digest()}"
