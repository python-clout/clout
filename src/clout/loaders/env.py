import os
import typing as t

import attr
import glom
import inflection
import marshmallow

from .. import schemas
from .. import util


@attr.dataclass
class Env:
    app_name: str = None
    metadata_key: str = "env"
    inherits: t.List[str] = frozenset({"app_name"})
    env: t.Dict[str, t.Any] = attr.ib(factory=dict)
    prefix: str = ""

    def make_path_to_field(
        self, schema: marshmallow.Schema, path=()
    ) -> t.Dict[str, marshmallow.fields.Field]:

        d = {}
        for field in schema.fields.values():

            if isinstance(field, marshmallow.fields.Nested):
                recursed = self.make_path_to_field(
                    field.schema, path=path + (field.name,)
                )
                d.update(recursed)

            elif isinstance(field, marshmallow.fields.Field):
                d[path + (field.name,)] = field
            else:
                raise TypeError(field)

        return d

    def make_envvar_name(self, path: t.Tuple[str]) -> str:
        prefix = self.prefix if self.prefix else self.app_name
        return inflection.underscore("_".join((prefix,) + path)).upper()

    def prep(self, typ, metadata=None, default=None, env=None, name=None):
        # TODO make sure this handles lists correctly.
        # If a field is a list, this should return a list, not a single member.
        metadata = metadata or {}
        top_name = name or util.dasherize(typ.__name__)

        schema = schemas.class_schema(typ)()
        path_to_field = self.make_path_to_field(schema, path=(top_name,))

        d = {}
        for path, field in path_to_field.items():
            name = self.make_envvar_name(path)

            value = os.environ.get(name)
            if value is not None:
                d[path] = field.deserialize(value)

        nested = make_nested(d)

        try:
            return nested
        except KeyError:
            return {}

    def set(self, **kw):
        return attr.evolve(self, **kw)


def make_nested(path_to_value: t.Dict[t.Tuple, t.Any]) -> dict:
    d = {}
    for path, value in sorted(
        path_to_value.items(), key=lambda path_value: len(path_value[0])
    ):
        func = dict
        glom.assign(d, ".".join(path), value, missing=func)
    return d


def load_env(type, prefix=""):
    return Env(prefix=prefix).prep(type)
