from model2data._vendor.pydbml.classes import Expression
from model2data._vendor.pydbml.renderer.sql.default.renderer import DefaultSQLRenderer


@DefaultSQLRenderer.renderer_for(Expression)
def render_expression(model: Expression) -> str:
    return f'({model.text})'
