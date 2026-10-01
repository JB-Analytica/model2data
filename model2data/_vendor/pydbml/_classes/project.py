from typing import Dict
from typing import Optional
from typing import Union

from model2data._vendor.pydbml._classes.base import DBMLObject
from model2data._vendor.pydbml._classes.note import Note


class Project(DBMLObject):
    dont_compare_fields = ('database',)

    def __init__(self,
                 name: str,
                 items: Optional[Dict[str, str]] = None,
                 note: Optional[Union[Note, str]] = None,
                 comment: Optional[str] = None):
        self.database = None
        self.name = name
        self.items = items or {}
        self.note = Note(note)
        self.comment = comment

    def __repr__(self):
        """<Project 'myproject'>"""
        return f'<Project {self.name!r}>'

    @property
    def note(self):
        return self._note

    @note.setter
    def note(self, val: Note) -> None:
        self._note = val
        val.parent = self
