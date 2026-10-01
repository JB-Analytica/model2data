"""Validation issues carry the line of the node their path points at."""

from model2data.model import Issue, validate
from model2data.model._yaml import _CoreLoader, _line_of_path, locate_issues

DOC = """\
model2data: 0.2.0
name: shop
tables:
  raw.users:
    columns:
      id: {type: bigint, pk: true}
      email: {type: email, bogus: 1}
  orders:
    columns:
      id: {type: bigint, pk: maybe}
    keys:
      - unique: [id]
      - unique: 5
"""


def _line(path, text=DOC):
    loader = _CoreLoader(text)
    try:
        return _line_of_path(loader.get_single_node(), path)
    finally:
        loader.dispose()


def test_a_simple_path_resolves_to_its_key_line():
    assert _line("tables.orders.columns.id.pk") == 10
    assert _line("name") == 2


def test_a_table_name_with_a_dot_resolves_by_longest_key():
    assert _line("tables.raw.users.columns.email") == 7


def test_a_sequence_index_resolves_in_either_spelling():
    assert _line("tables.orders.keys.1") == 13
    assert _line("tables.orders.keys.[1]") == 13
    assert _line("tables.orders.keys.1.unique") == 13


def test_an_unresolvable_tail_falls_back_to_the_deepest_node():
    assert _line("tables.orders.columns.nope.type") == 9
    assert _line("tables.orders.keys.9") == 11
    assert _line("nothing.at.all") is None
    assert _line("") is None


def test_validate_gives_issues_their_lines():
    issues = validate(DOC)
    by_path = {issue.path: issue for issue in issues}
    assert by_path["tables.raw.users.columns.email.bogus"].line == 7
    assert by_path["tables.orders.columns.id.pk"].line == 10
    assert "(line 10)" in str(by_path["tables.orders.columns.id.pk"])


def test_json_issues_get_lines_too():
    text = '{\n  "model2data": "0.2.0",\n  "name": "m",\n  "tables": {\n    "t": {"columns": {"id": {"type": "bigint", "pk": "maybe"}}}\n  }\n}'
    issues = validate(text)
    assert [issue.line for issue in issues] == [5]


def test_dbml_issues_keep_no_line():
    assert all(issue.line is None for issue in validate("Table t { id int }\n", format="dbml"))


def test_a_sequence_followed_by_a_name_stops_at_the_sequence():
    # `keys` is a list: a path that names a key inside it cannot be followed.
    assert _line("tables.orders.keys.unique") == 11


def test_a_path_past_a_plain_value_stops_at_that_value():
    assert _line("name.first") == 2


def test_text_that_does_not_compose_leaves_the_issues_as_they_were():
    issue = Issue("tables", "broken")
    assert locate_issues("a: [unclosed", [issue]) == [issue]


def test_an_empty_document_leaves_the_issues_as_they_were():
    issue = Issue("tables", "missing")
    assert locate_issues("", [issue]) == [issue]
