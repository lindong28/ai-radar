from airadar.interpret.engine.summarizer.slug import slugify_title


def test_slugify_english_title_uses_underscores() -> None:
    assert slugify_title("Building Effective Agents!") == "Building_Effective_Agents"


def test_slugify_chinese_title_removes_spaces_and_punctuation() -> None:
    assert slugify_title("软件工程师 头衔要没了：Claude Code 之父访谈") == "软件工程师头衔要没了_Claude_Code_之父访谈"


def test_slugify_truncates_without_trailing_separator() -> None:
    assert slugify_title("Alpha Beta Gamma Delta", max_length=12) == "Alpha_Beta"
