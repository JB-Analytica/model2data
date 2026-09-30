# model2data: `Records` blocks, which PyDBML 1.2.1 could not read.
#
#   Records statuses(id, label) {        records (id, label) {   <- inside a table
#     1, 'open'                            1, 'open'
#     2, 'closed'                        }
#   }
#
# A value is a string, a number, true/false/null, or an `expression`, which
# is kept as {'expression': text}. Without a column list the columns are the
# table's own, in order.
import pyparsing as pp

from .common import _
from .common import _c
from .common import end
from .generic import expression_literal
from .generic import name
from .generic import number_literal
from .generic import string_literal
from model2data._vendor.pydbml.parser.blueprints import RecordsBlueprint

pp.ParserElement.set_default_whitespace_chars(' \t\r')

_NULL = object()

record_value = (
    string_literal
    | expression_literal.copy().set_parse_action(lambda s, loc, tok: {'expression': tok[0]})
    | number_literal.copy().set_parse_action(
        lambda s, loc, tok: float(tok[0]) if any(ch in tok[0] for ch in '.eE') else int(tok[0])
    )
    | pp.CaselessKeyword('true').set_parse_action(lambda s, loc, tok: True)
    | pp.CaselessKeyword('false').set_parse_action(lambda s, loc, tok: False)
    # A parse action returning None leaves the token in place, hence _NULL.
    | pp.CaselessKeyword('null').set_parse_action(lambda s, loc, tok: _NULL)
)
record_row = pp.Group(pp.DelimitedList(record_value) + pp.Suppress(',')[0, 1])
record_rows = (_ + record_row + _)[...]
record_columns = '(' + _ + pp.Group(pp.DelimitedList(_ + name + _))('columns') + _ + ')'

records_table = (name('schema') + '.' + name('table')) | name('table')


def _records(s, loc, tok):
    rows = [
        [None if value is _NULL else value for value in row]
        for row in tok.get('rows', [])
    ]
    init = {
        'columns': list(tok['columns']) if 'columns' in tok else [],
        'rows': rows,
    }
    if 'table' in tok:
        init['table'] = tok['table']
    if 'schema' in tok:
        init['schema'] = tok['schema']
    return RecordsBlueprint(**init)


records = _c + (
    pp.CaselessKeyword('records').suppress()
    + records_table
    + record_columns[0, 1] + _
    + '{' - pp.Group(record_rows)('rows') + _ + '}'
) + end
records.set_parse_action(lambda s, loc, tok: _records(s, loc, _flatten_rows(tok)))

table_records = (
    pp.CaselessKeyword('records').suppress()
    + record_columns[0, 1] + _
    + '{' - pp.Group(record_rows)('rows') + _ + '}'
)
table_records.set_parse_action(lambda s, loc, tok: _records(s, loc, _flatten_rows(tok)))


def _flatten_rows(tok):
    # `rows` is one group holding the row groups; hand _records the rows.
    result = {key: tok[key] for key in ('columns', 'table', 'schema') if key in tok}
    result['rows'] = list(tok['rows']) if 'rows' in tok else []
    return result
