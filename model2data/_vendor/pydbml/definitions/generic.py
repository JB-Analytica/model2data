import pyparsing as pp

from model2data._vendor.pydbml.parser.blueprints import ExpressionBlueprint

pp.ParserElement.set_default_whitespace_chars(' \t\r')

name = pp.Word(pp.alphanums + '_') | pp.QuotedString('"')

# Literals

string_literal = (
    pp.QuotedString("'", escChar="\\")
    ^ pp.QuotedString('"', escChar="\\")
    ^ pp.QuotedString("'''", escChar="\\", multiline=True)
)
expression_literal = pp.Combine(
    pp.Suppress('`')
    + pp.CharsNotIn('`')[...]
    + pp.Suppress('`')
).set_parse_action(lambda s, lok, tok: ExpressionBlueprint(tok[0]))

boolean_literal = (
    pp.CaselessLiteral('true')
    | pp.CaselessLiteral('false')
    | pp.CaselessLiteral('NULL')
)
# model2data: an optional sign and an exponent, as DBML allows (`-5`, `1e3`,
# `-2.5E-3`). 1.2.1 read only unsigned `12` and `1.5`.
number_literal = pp.Regex(r'[-+]?\d+(\.\d+)?([eE][-+]?\d+)?')

# Expression

expr_chars = pp.Word(pp.alphanums + "\"'`,._+- \n\t")
expr_chars_no_comma_space = pp.Word(pp.alphanums + "\"'`._+-")
expression = pp.Forward()
factor = (
    pp.Word(pp.alphanums + '_')[0, 1] + '(' + expression + ')'
    | expr_chars_no_comma_space + (pp.Literal(",") | ");" | (pp.LineEnd() + ");"))
    | expr_chars
)
expression << factor[...]
