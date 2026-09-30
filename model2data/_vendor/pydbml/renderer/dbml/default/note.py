from textwrap import indent

from model2data._vendor.pydbml.classes import Note
from model2data._vendor.pydbml.renderer.dbml.default.renderer import DefaultDBMLRenderer
from model2data._vendor.pydbml.renderer.dbml.default.utils import quote_string


@DefaultDBMLRenderer.renderer_for(Note)
def render_note(model: Note) -> str:
    text = quote_string(model.text)

    text = indent(text, '    ')
    result = f'Note {{\n{text}\n}}'
    return result
