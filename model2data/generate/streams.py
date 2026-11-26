"""Random streams: what a seeded run draws from, and who draws from which.

A column's values come from a stream of its own, derived from the run's seed, the
table (and its `table_seeds` entry), a kind of draw and a name -- the column's, the
rule's, the key's. Nothing else goes into it, so the values of a column depend on
the run's settings and the column's own definition (and on the columns and parents
it is derived from, ordered after or ruled with), never on which other columns the
table holds or where in the table it sits.

Two random generators are in play, and both are seeded together and put back
together: the `random` module's, and the one every Faker instance shares.

With no seed there is nothing to derive a stream from, so nothing here reseeds.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Optional

import faker.generator
from faker import Faker


def digest_seed(payload: str) -> int:
    """An RNG seed from text.

    Hashed with blake2b rather than Python's built-in `hash()`, which is salted
    per interpreter process for strings: a "deterministic" seed built on it
    would reproduce only within a single run of the program, which is the one
    place determinism was never in doubt.
    """
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def stream_seed(
    seed: int, table: str, table_seed: Optional[int], kind: str = "", name: str = ""
) -> int:
    """The seed of one stream: the run's, the table's, and what is drawn (`kind`, `name`).

    The parts are joined with `|`, so a name cannot be mistaken for another kind.
    With no `kind` it is the table's own stream (`table_seeds` re-rolls it).
    """
    payload = f"{seed}|{table}|{'' if table_seed is None else table_seed}"
    if kind:
        payload += f"|{kind}|{name}"
    return digest_seed(payload)


def seed_both(seed: Optional[int]) -> None:
    """Seed the `random` module and the generator every Faker instance shares."""
    random.seed(seed)
    Faker.seed(seed)


class Streams:
    """The streams of one table: `reseed(kind, name)` starts the one for what is drawn next.

    `scope` tells apart one table's streams on another day (see generate.days). With no
    seed every method does nothing, and a run draws as it always has: from chance.
    """

    def __init__(
        self,
        seed: Optional[int],
        table: str,
        table_seed: Optional[int] = None,
        scope: str = "",
    ) -> None:
        self.seed = seed
        self.table = table
        self.table_seed = table_seed
        self.scope = scope

    def seed_for(self, kind: str, name: str) -> Optional[int]:
        """The seed of the stream of `name` under `kind` (None without a run seed)."""
        if self.seed is None:
            return None
        return stream_seed(self.seed, self.table, self.table_seed, self.scope + kind, name)

    def reseed(self, kind: str, name: str) -> None:
        """Draw what comes next from the stream of `name` under `kind`, from its start."""
        stream = self.seed_for(kind, name)
        if stream is not None:
            seed_both(stream)


@contextmanager
def own_stream(seed: Optional[int]) -> Iterator[None]:
    """Draw from a stream of `seed` inside the block, and put the shared streams back after.

    Both streams generation draws from -- the `random` module's and the one
    every Faker instance shares -- are seeded on the way in and restored on the
    way out, so what is drawn here never shifts a draw made outside.
    """
    shared = random.getstate()
    faker_random = faker.generator.random
    faker_state = faker_random.getstate()
    try:
        random.seed(seed)
        faker_random.seed(seed)
        yield
    finally:
        random.setstate(shared)
        faker_random.setstate(faker_state)


class Resumable:
    """Streams that carry on where they stopped, one for each key.

    A person pool grows when a column asks for more rows than it holds, and a rule
    draws again round after round: each takes up its own stream where it left it, so
    what it draws does not depend on who asked first, or on what else was drawn
    in between. `seed_for(key)` seeds a key's stream when it first gets one.
    """

    def __init__(self, seed_for: Callable[[Any], Optional[int]]) -> None:
        self._seed_for = seed_for
        self._held: dict[Any, tuple[tuple, tuple]] = {}

    @contextmanager
    def use(self, key: Any) -> Iterator[None]:
        shared = random.getstate()
        faker_random = faker.generator.random
        faker_state = faker_random.getstate()
        try:
            held = self._held.get(key)
            if held is None:
                stream = self._seed_for(key)
                random.seed(stream)
                faker_random.seed(stream)
            else:
                random.setstate(held[0])
                faker_random.setstate(held[1])
            yield
        finally:
            self._held[key] = (random.getstate(), faker_random.getstate())
            random.setstate(shared)
            faker_random.setstate(faker_state)

    def drop(self, predicate: Callable[[Any], bool]) -> None:
        for key in [key for key in self._held if predicate(key)]:
            del self._held[key]
