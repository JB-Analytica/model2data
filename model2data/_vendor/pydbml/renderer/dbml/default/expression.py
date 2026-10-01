from model2data._vendor.pydbml.classes import Expression
from model2data._vendor.pydbml.renderer.dbml.default.renderer import DefaultDBMLRenderer


@DefaultDBMLRenderer.renderer_for(Expression)
def render_expression(model: Expression) -> str:
    return f'`{model.text}`'
