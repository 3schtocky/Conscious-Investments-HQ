from hq.sourcemap import SourcePool, exceptions, extract_figures, tie_out


def texts(s):
    return [f.text for f in extract_figures(s)]


def test_extracts_figures_and_skips_noise():
    s = "In Q3 2025 revenue rose 12.5% to $1.2 billion [1]. We count 3 segments on March 5, 2026. Target is $114.31 at 8.5x."
    assert texts(s) == ["12.5%", "$1.2 billion", "$114.31", "8.5x"]


def test_matches_with_rounding_and_scale():
    pool = SourcePool()
    pool.add_number(1_234_567_890, "model")
    pool.add_number(0.125, "facts")
    pool.add_number(114.309, "model")
    figs = extract_figures("Revenue was $1.2 billion, up 12.5%, target $114.31, margin 9.9%.")
    ties = tie_out(figs, pool)
    assert [t.source for t in ties] == ["model", "facts", "model", None]
    assert [f.text for f in exceptions(ties)] == ["9.9%"]


def test_pool_reads_json_and_csv():
    pool = SourcePool()
    pool.add_source("model.json", '{"scenarios": {"base": {"price_target": 114.31}}}')
    pool.add_source("p.csv", "a,b\n1,106.76\n")
    ties = tie_out(extract_figures("Base $114.31 versus $106.76 and $200.00."), pool)
    assert [t.source is None for t in ties] == [False, False, True]


def test_derived_figures_clear_by_formula_or_pair():
    pool = SourcePool()
    pool.add_number(807_000_000, "model")
    pool.add_number(3.4, "model")
    s = "Backlog of $807 mn over 3.4 GWh is about $237 per kWh (our arithmetic: $807 mn / 3.4 GWh)."
    ties = tie_out(extract_figures(s), pool)
    assert all(t.source for t in ties) and any(t.source.startswith("derived") for t in ties)
    margin = "Q2 gross margin would be about -89% (our arithmetic: (-48.8 - 12.5) / 68.8)."
    ties = {t.figure.text: t.source for t in tie_out(extract_figures(margin), SourcePool())}
    assert ties["-89%"].startswith("derived") and ties["68.8"] is None   # the operand still needs its own source


def test_wrong_arithmetic_is_still_an_exception():
    pool = SourcePool()
    pool.add_number(807_000_000, "model")
    pool.add_number(3.4, "model")
    ties = tie_out(extract_figures("Backlog of $807 mn over 3.4 GWh is about $300 per kWh (our arithmetic: $807 mn / 3.4 GWh)."), pool)
    assert [t.figure.text for t in ties if t.source is None] == ["$300"]


def test_pair_arithmetic_without_a_formula():
    pool = SourcePool()
    pool.add_number(120.0, "model")
    pool.add_number(100.0, "model")
    ties = tie_out(extract_figures("Revenue of $120 against $100 last year is up 20%."), pool)
    assert all(t.source for t in ties)
