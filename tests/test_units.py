"""Small units: matching rules, formula evaluation, Markdown rendering."""

from fakepii_docs.extract import evaluate_formula, render_markdown
from fakepii_docs.people import make_plan, split_points
from fakepii_docs.textmatch import find_spans, normalize


def test_normalize_whitespace_and_zero_width():
    assert normalize("Ann \n\tEx​ample­") == "Ann Example"
    assert find_spans(normalize("x ANN@EXAMPLE.COM, y"), "ann@example.com", ignore_case=True) == [(2, 17)]
    assert find_spans("Ann Ann", "Ann") == [(0, 3), (4, 7)]


def test_formula_evaluator():
    values = {"S": {"A1": "Ann", "B1": "Example"}, "Other Sheet": {"C3": "x"}}
    assert evaluate_formula('=_xlfn.CONCAT(A1," ",B1)', "S", values) == "Ann Example"
    assert evaluate_formula('=CONCATENATE("A","nn ","Exa","mple")', "S", values) == "Ann Example"
    assert evaluate_formula('=A1&" "&$B$1', "S", values) == "Ann Example"
    assert evaluate_formula("='Other Sheet'!C3&S!A1", "S", values) == "xAnn"
    assert evaluate_formula('="say ""hi"""', "S", values) == 'say "hi"'
    assert evaluate_formula("=SUM(A1:A3)", "S", values) is None
    assert evaluate_formula("=A1+1", "S", values) is None


def test_markdown_rendering_keeps_line_numbers_and_hides_targets():
    text = ("---\nauthor: Ann Example\n---\n**A**nn *Example* [mail](mailto:a@example.com)\n"
            "![Photo of Ann](ann.png) <!-- by Bo -->\nAnn&nbsp;Example \\*x\\*\n")
    rendered, hidden = render_markdown(text)
    lines = rendered.split("\n")
    assert lines[3] == "Ann Example mail"
    assert lines[4].strip() == ""
    assert lines[5] == "Ann Example *x*"
    got = {(s.path, s.text) for s in hidden}
    assert ("front_matter/author", "Ann Example") in got
    assert ("line[4]/link[1]@target", "mailto:a@example.com") in got
    assert ("line[5]/image[1]@alt", "Photo of Ann") in got
    assert ("line[5]/comment", " by Bo ") in got


def test_plan_is_seeded_and_fake():
    a, b = make_plan(3, "docx"), make_plan(3, "docx")
    assert a.people == b.people and a.near_misses == b.near_misses
    assert make_plan(3, "xlsx").people != a.people
    for p in a.people:
        assert p.ssn[:3] == "000" or p.ssn[0] == "9"
        assert "555-01" in p.phone
    import random
    assert "".join(split_points("Ann Example", random.Random(1), 3)) == "Ann Example"
