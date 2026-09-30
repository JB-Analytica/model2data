from __future__ import annotations

from io import TextIOWrapper
from pathlib import Path
from typing import List
from typing import Optional
from typing import Type
from typing import Union

import pyparsing as pp

from model2data._vendor.pydbml.classes import Table
from model2data._vendor.pydbml.database import Database
from model2data._vendor.pydbml.definitions.common import comment
from model2data._vendor.pydbml.definitions.enum import enum
from model2data._vendor.pydbml.definitions.project import project
from model2data._vendor.pydbml.definitions.reference import ref
from model2data._vendor.pydbml.definitions.sticky_note import sticky_note
from model2data._vendor.pydbml.definitions.table import table, table_with_properties
from model2data._vendor.pydbml.definitions.table_group import table_group
from model2data._vendor.pydbml.definitions.records import records
from model2data._vendor.pydbml.definitions.table_partial import table_partial
from model2data._vendor.pydbml.exceptions import TableNotFoundError
from model2data._vendor.pydbml.renderer.base import BaseRenderer
from model2data._vendor.pydbml.renderer.dbml.default import DefaultDBMLRenderer
from model2data._vendor.pydbml.renderer.sql.default import DefaultSQLRenderer
from model2data._vendor.pydbml.tools import remove_bom
from .blueprints import EnumBlueprint, StickyNoteBlueprint
from .blueprints import ProjectBlueprint
from .blueprints import ReferenceBlueprint
from .blueprints import TableBlueprint
from .blueprints import TableGroupBlueprint
from .blueprints import ColumnBlueprint
from .blueprints import PartialRefBlueprint
from .blueprints import RecordsBlueprint
from .blueprints import TablePartialBlueprint
from model2data._vendor.pydbml.exceptions import ValidationError
import dataclasses

pp.ParserElement.set_default_whitespace_chars(" \t\r")


class PyDBML:
    """
    PyDBML parser factory. If properly initiated, returns parsed Database.

    Usage option 1:

    >>> with open('test_schema.dbml') as f:
    ...     p = PyDBML(f)
    ...     # or
    ...     p = PyDBML(f.read())

    Usage option 2:
    >>> p = PyDBML.parse_file('test_schema.dbml')
    >>> # or
    >>> from pathlib import Path
    >>> p = PyDBML(Path('test_schema.dbml'))
    """

    def __new__(
        cls,
        source_: Optional[Union[str, Path, TextIOWrapper]] = None,
        allow_properties: bool = False,
        sql_renderer: Type[BaseRenderer] = DefaultSQLRenderer,
        dbml_renderer: Type[BaseRenderer] = DefaultDBMLRenderer,
    ):
        if source_ is not None:
            if isinstance(source_, str):
                source = source_
            elif isinstance(source_, Path):
                with open(source_, encoding="utf8") as f:
                    source = f.read()
            elif isinstance(source_, TextIOWrapper):
                source = source_.read()
            else:
                raise TypeError("Source must be str, path or file stream")

            source = remove_bom(source)
            return cls.parse(
                source,
                allow_properties=allow_properties,
                sql_renderer=sql_renderer,
                dbml_renderer=dbml_renderer,
            )
        else:
            return super().__new__(cls)

    def __repr__(self):
        return "<PyDBML>"

    @staticmethod
    def parse(
        text: str,
        allow_properties: bool = False,
        sql_renderer: Type[BaseRenderer] = DefaultSQLRenderer,
        dbml_renderer: Type[BaseRenderer] = DefaultDBMLRenderer,
    ) -> Database:
        text = remove_bom(text)
        parser = PyDBMLParser(
            text,
            allow_properties=allow_properties,
            sql_renderer=sql_renderer,
            dbml_renderer=dbml_renderer,
        )
        return parser.parse()

    @staticmethod
    def parse_file(file: Union[str, Path, TextIOWrapper]) -> Database:
        if isinstance(file, TextIOWrapper):
            source = file.read()
        else:
            with open(file, encoding="utf8") as f:
                source = f.read()
        source = remove_bom(source)
        parser = PyDBMLParser(source)
        return parser.parse()


class PyDBMLParser:
    def __init__(
        self,
        source: str,
        allow_properties: bool = False,
        sql_renderer: Type[BaseRenderer] = DefaultSQLRenderer,
        dbml_renderer: Type[BaseRenderer] = DefaultDBMLRenderer,
    ):
        self.database = None

        self.ref_blueprints: List[ReferenceBlueprint] = []
        self.table_groups: List[TableGroupBlueprint] = []
        self.source = source
        self.tables: List[TableBlueprint] = []
        self.refs: List[ReferenceBlueprint] = []
        self.enums: List[EnumBlueprint] = []
        self.project: Optional[ProjectBlueprint] = None
        self.sticky_notes: List[StickyNoteBlueprint] = []
        # model2data
        self.partials: dict = {}
        self.records: List[RecordsBlueprint] = []
        self._allow_properties = allow_properties
        self._sql_renderer = sql_renderer
        self._dbml_renderer = dbml_renderer

    def parse(self):
        self._set_syntax()
        self._syntax.parse_string(self.source, parseAll=True)
        self.build_database()
        return self.database

    def __repr__(self):
        return "<PyDBMLParser>"

    def _set_syntax(self):
        table_expr = (
            table_with_properties.copy() if self._allow_properties else table.copy()
        )
        ref_expr = ref.copy()
        enum_expr = enum.copy()
        table_group_expr = table_group.copy()
        project_expr = project.copy()
        note_expr = sticky_note.copy()
        partial_expr = table_partial.copy()
        records_expr = records.copy()

        table_expr.addParseAction(self.parse_blueprint)
        ref_expr.addParseAction(self.parse_blueprint)
        enum_expr.addParseAction(self.parse_blueprint)
        table_group_expr.addParseAction(self.parse_blueprint)
        project_expr.addParseAction(self.parse_blueprint)
        note_expr.addParseAction(self.parse_blueprint)
        partial_expr.addParseAction(self.parse_blueprint)
        records_expr.addParseAction(self.parse_blueprint)

        expr = (
            table_expr
            | ref_expr
            | enum_expr
            | table_group_expr
            | project_expr
            | note_expr
            | partial_expr
            | records_expr
        )
        self._syntax = expr[...] + ("\n" | comment)[...] + pp.StringEnd()

    def parse_blueprint(self, s, loc, tok):
        blueprint = tok[0]
        if isinstance(blueprint, TableBlueprint):
            self.tables.append(blueprint)
            ref_bps = blueprint.get_reference_blueprints()
            col_bps = blueprint.columns or []
            index_bps = blueprint.indexes or []
            for ref_bp in ref_bps:
                self.refs.append(ref_bp)
                ref_bp.parser = self
            for col_bp in col_bps:
                col_bp.parser = self
                if isinstance(col_bp, PartialRefBlueprint):
                    continue
                if col_bp.note:
                    col_bp.note.parser = self
            for index_bp in index_bps:
                index_bp.parser = self
                if index_bp.note:
                    index_bp.note.parser = self
            if blueprint.note:
                blueprint.note.parser = self
        elif isinstance(blueprint, ReferenceBlueprint):
            self.refs.append(blueprint)
        elif isinstance(blueprint, EnumBlueprint):
            self.enums.append(blueprint)
            for enum_item in blueprint.items:
                if enum_item.note:
                    enum_item.note.parser = self
        elif isinstance(blueprint, TableGroupBlueprint):
            self.table_groups.append(blueprint)
        elif isinstance(blueprint, ProjectBlueprint):
            self.project = blueprint
            if blueprint.note:
                blueprint.note.parser = self
        elif isinstance(blueprint, StickyNoteBlueprint):
            self.sticky_notes.append(blueprint)
        elif isinstance(blueprint, TablePartialBlueprint):
            if blueprint.name in self.partials:
                raise ValidationError(f'TablePartial {blueprint.name} is defined twice')
            self.partials[blueprint.name] = blueprint
            for col_bp in blueprint.columns or []:
                col_bp.parser = self
                if col_bp.note:
                    col_bp.note.parser = self
            for index_bp in blueprint.indexes or []:
                index_bp.parser = self
        elif isinstance(blueprint, RecordsBlueprint):
            self.records.append(blueprint)
        else:
            raise RuntimeError(f"type unknown: {blueprint}")
        blueprint.parser = self

    def locate_table(self, schema: str, name: str) -> "Table":
        if not self.database:
            raise RuntimeError("Database is not ready")
        # first by alias
        result = self.database.table_dict.get(name)
        if result is None:
            full_name = f"{schema}.{name}"
            result = self.database.table_dict.get(full_name)
        if result is None:
            raise TableNotFoundError(f"Table {full_name} not present in the database")
        return result

    def _inject_partials(self, table_bp: TableBlueprint) -> None:
        """Replace each `~name` in a table with the partial's columns (model2data).

        As DBML defines it: a partial's columns land where `~name` stands; a
        column the table declares itself wins over a partial's, and between
        partials the later one wins, at the place the name first appeared.
        The partials' indexes follow the table's own, and their colour and
        note apply when the table sets none. Inline refs on injected columns
        are registered for this table.
        """
        items = table_bp.columns or []
        if not any(isinstance(item, PartialRefBlueprint) for item in items):
            return
        own = {item.name for item in items if isinstance(item, ColumnBlueprint)}
        placed: dict = {}
        result: list = []
        indexes = list(table_bp.indexes or [])
        for item in items:
            if isinstance(item, ColumnBlueprint):
                result.append(item)
                continue
            partial = self.partials.get(item.name)
            if partial is None:
                raise ValidationError(
                    f'Table {table_bp.name} uses ~{item.name}, but no TablePartial '
                    f'{item.name} is defined'
                )
            for column in partial.columns or []:
                if column.name in own:
                    continue
                copy = dataclasses.replace(
                    column,
                    ref_blueprints=[dataclasses.replace(ref) for ref in column.ref_blueprints or []],
                )
                copy.parser = self
                for ref_bp in copy.ref_blueprints:
                    ref_bp.schema1 = table_bp.schema
                    ref_bp.table1 = table_bp.name
                    ref_bp.col1 = copy.name
                    ref_bp.parser = self
                    self.refs.append(ref_bp)
                if column.name in placed:
                    result[placed[column.name]] = copy
                else:
                    placed[column.name] = len(result)
                    result.append(copy)
            indexes.extend(dataclasses.replace(index) for index in partial.indexes or [])
            if partial.header_color and not table_bp.header_color:
                table_bp.header_color = partial.header_color
            if partial.note and not table_bp.note:
                table_bp.note = partial.note
        table_bp.columns = result
        table_bp.indexes = indexes

    def _attach_records(self) -> None:
        """Hand each top-level `Records` block to its table (model2data)."""
        tables = {}
        for table_bp in self.tables:
            tables[(table_bp.schema, table_bp.name)] = table_bp
            if table_bp.alias:
                tables[(table_bp.schema, table_bp.alias)] = table_bp
        for records_bp in self.records:
            table_bp = tables.get((records_bp.schema, records_bp.table))
            if table_bp is None:
                raise TableNotFoundError(
                    f'Records name table {records_bp.schema}.{records_bp.table}, '
                    'which is not defined'
                )
            table_bp.records = list(table_bp.records or []) + [records_bp]
        for table_bp in self.tables:
            names = [column.name for column in table_bp.columns or []]
            for records_bp in table_bp.records or []:
                if not records_bp.columns:
                    records_bp.columns = list(names)
                unknown = [name for name in records_bp.columns if name not in names]
                if unknown:
                    raise ValidationError(
                        f'Records for {table_bp.name} name columns it does not have: '
                        + ', '.join(unknown)
                    )
                for row in records_bp.rows:
                    if len(row) != len(records_bp.columns):
                        raise ValidationError(
                            f'A row of Records for {table_bp.name} has {len(row)} values '
                            f'for {len(records_bp.columns)} columns'
                        )

    def build_database(self):
        for table_bp in self.tables:
            self._inject_partials(table_bp)
        self._attach_records()
        self.database = Database(
            allow_properties=self._allow_properties,
            sql_renderer=self._sql_renderer,
            dbml_renderer=self._dbml_renderer,
        )
        for enum_bp in self.enums:
            self.database.add(enum_bp.build())
        for table_bp in self.tables:
            self.database.add(table_bp.build())
            self.ref_blueprints.extend(table_bp.get_reference_blueprints())
        for table_group_bp in self.table_groups:
            self.database.add(table_group_bp.build())
        for note_bp in self.sticky_notes:
            self.database.add(note_bp.build())
        if self.project:
            self.database.add(self.project.build())
        for ref_bp in self.refs:
            self.database.add(ref_bp.build())
