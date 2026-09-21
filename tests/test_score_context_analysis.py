from evals._shared.score_context_analysis import measurement, source_role, train_case


def test_roles_are_channel_not_gold():
    for sid, expected in (("x_anthropicai", "organization-official"),
                          ("x_sama", "individual-media-aggregator"), ("new", "unknown")):
        assert source_role({"source_id": sid, "reference": {"score": 100}}) == expected


def test_source_split_ignores_labels_and_item_identity():
    outcomes = set()
    for sid in ("x_sama", "x_anthropicai", "openai_blog", "buzzing_hn", "ithome", "google_ai"):
        a = {"case_id": "a", "input": {"source_id": sid}, "reference": {"score": 0}}
        b = {"case_id": "b", "input": {"source_id": sid}, "reference": {"score": 100}}
        assert train_case(a) == train_case(b)
        outcomes.add(train_case(a))
    assert outcomes == {True, False}


def test_metrics_reuse_canonical_score():
    cases = [{"case_id": str(i), "input": {}, "reference": {"score": v}} for i, v in enumerate((10, 20, 30))]
    same = measurement(cases, [10, 20, 30])
    reverse = measurement(cases, [30, 20, 10])
    assert same["metrics"]["mae"]["value"] == 0
    assert same["metrics"]["spearman"]["value"] == 1
    assert reverse["metrics"]["mae"]["value"] > 0
    assert reverse["metrics"]["spearman"]["value"] == -1
