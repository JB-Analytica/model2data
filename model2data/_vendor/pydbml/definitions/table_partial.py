# model2data: `TablePartial`, which PyDBML 1.2.1 could not read.
#
#   TablePartial audited [headercolor: #FF0000] {
#     created_at timestamp [not null]
#   }
#   Table users {
#     id int [pk]
#     ~audited          <- the partial's columns, indexes and settings, here
#   }
#
# The injection itself happens in PyDBMLParser.build_database, once every
# partial has been read, since a table may use one declared after it.
import pyparsing as pp

from .common import _
from .common import _c
from .common import end
from .generic import name
from .table import table_body
from .table import table_settings
from model2data._vendor.pydbml.parser.blueprints import ColumnBlueprint
from model2data._vendor.pydbml.parser.blueprints import TablePartialBlueprint

pp.ParserElement.set_default_whitespace_chars(' \t\r')

table_partial = _c + (
    pp.CaselessKeyword('TablePartial').suppress()
    + name('name')
    + table_settings('settings')[0, 1] + _
    + '{' - table_body + _ + '}'
) + end


def parse_table_partial(s, loc, tok):
    init = {'name': tok['name']}
    if 'settings' in tok:
        init.update(tok['settings'])
    if 'note' in tok:
        init['note'] = tok['note'][0]
    if 'indexes' in tok:
        init['indexes'] = tok['indexes'][0]
    init['columns'] = [
        column for column in tok.get('columns', []) if isinstance(column, ColumnBlueprint)
    ]
    return TablePartialBlueprint(**init)


table_partial.set_parse_action(parse_table_partial)
