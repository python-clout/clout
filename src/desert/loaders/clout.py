import collections
import functools
import math
import subprocess
import typing as t

import attr
import click
import lark


ALWAYS_ACCEPT = True


class CountingBaseCommand:
    def __init__(self, *args, nargs=1, required=False, **kwargs):
        self.nargs = nargs
        self.required = required

        super().__init__(*args, **kwargs)

    @property
    def multiple(self):
        return self.nargs != 1


class CountingGroup(CountingBaseCommand, click.Group):
    pass


class CountingCommand(CountingBaseCommand, click.Command):
    pass


@functools.singledispatch
def to_lark(obj: object):
    raise NotImplementedError(obj)


def quote(s):
    return '"' + s + '"'


def par(s):
    return f"({s})"


def one_of(items):
    items = list(items)
    if len(items) == 1:
        return str(items[0])
    return par(" | ".join(items))


def name_rule(obj) -> str:
    return f"{obj.name}_{id(obj)}"


@to_lark.register
def _(option: click.Option):
    if option.is_flag:
        return (
            f"{name_rule(option)} : "
            + "|".join(quote(decl) for decl in option.opts)
            + "\n"
        )
    return (
        f"{name_rule(option)} : "
        + one_of(quote(decl) + ' "="? value' for decl in option.opts)
        + "\n"
    )


@to_lark.register
def _(arg: click.Argument):
    return f"{name_rule(arg)} : value\n"


def min_params(cmd: click.BaseCommand) -> int:
    return sum(
        1 if p.nargs == -1 or p.multiple else p.nargs
        for p in cmd.params
        if p.required or not p.default
    )


def max_params(cmd: click.BaseCommand) -> t.Union[int, float]:
    return sum(math.inf if p.nargs == -1 or p.multiple else p.nargs for p in cmd.params)


@to_lark.register
def _(cmd: CountingCommand):
    optionals = [name_rule(p) for p in cmd.params if not p.required or p.default]
    requireds = [name_rule(p) for p in cmd.params if p.required]
    params = one_of(optionals + requireds)

    if max_params(cmd) == math.inf:
        params += f"+"
    else:
        params += f" ~ {min_params(cmd)}..{max_params(cmd)}"
    out = f"{name_rule(cmd)} : {quote(cmd.name)} {params} \n"
    for p in cmd.params:
        out += to_lark(p)
    return out


@to_lark.register
def _(grp: CountingGroup):
    if not grp.commands:
        return to_lark.dispatch(CountingCommand)(grp)
    command = one_of(name_rule(c) for c in grp.commands.values())

    optionals = [name_rule(p) for p in grp.params if not p.required]
    requireds = [name_rule(p) for p in grp.params if p.required]
    params = one_of(optionals + requireds)

    if grp.params:
        out = f"{name_rule(grp)} : {quote(grp.name)} ({command}|{params})+ \n"
    else:
        out = f"{name_rule(grp)} : {quote(grp.name)} {command}+ \n"

    for c in grp.commands.values():
        out += to_lark(c)
    for p in grp.params:
        out += to_lark(p)
    return out


def build_grammar(grp):
    grammar = to_lark(grp)
    grammar += "?value : /\S+/\n"
    grammar += f"?start : {name_rule(grp)}\n"
    grammar += "%import common.CNAME\n"
    grammar += "%import common.WS -> _WHITE\n"
    grammar += "%ignore _WHITE\n"

    return grammar


def get_base_commands(grp: click.BaseCommand) -> t.Iterator[click.BaseCommand]:
    yield grp
    if not hasattr(grp, "commands"):
        return
    for cmd in grp.commands.values():
        yield from get_base_commands(cmd)


class Walker(lark.Visitor):
    def __init__(self, *args, group, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)
        base_commands = list(get_base_commands(self.group))
        self.all_param_names = {name_rule(p) for c in base_commands for p in c.params}
        for cmd in base_commands:
            name, method = self.make_validation_method(cmd)
            setattr(self, name, method)

    def make_validation_method(self, command):
        def param_validation_method(parsed_command):

            counter = collections.Counter(p.data for p in parsed_command.children)

            for param_or_cmd in list(command.params) + list(
                getattr(command, "commands", {}).values()
            ):
                param_or_cmd_id = name_rule(param_or_cmd)
                observed = counter.get(param_or_cmd_id, 0)
                if param_or_cmd.required and observed == 0:

                    raise InvalidInput(param_or_cmd, observed)
                if (
                    not param_or_cmd.multiple
                    and param_or_cmd.nargs != -1
                    and observed != param_or_cmd.nargs
                    and not (not param_or_cmd.required and observed == 0)
                ):
                    raise InvalidInput(param_or_cmd, observed)

        return name_rule(command), param_validation_method


class InvalidInput(Exception):
    pass


class Transformer(lark.Transformer):
    def __init__(self, *args, group, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)
        base_commands = list(get_base_commands(self.group))
        self.all_param_names = {name_rule(p) for c in base_commands for p in c.params}
        for cmd in base_commands:
            if isinstance(cmd, CountingCommand) and not isinstance(
                cmd, click.MultiCommand
            ):
                # Not a group
                name, method = self.make_command_method(cmd)
                setattr(self, name, method)
            for param in cmd.params:
                name, method = self.make_param_method(cmd, param)
                setattr(self, name, method)

            if isinstance(cmd, click.MultiCommand):
                name, method = self.make_group_method(cmd)
                setattr(self, name, method)

    def process_params(self, command, parsed):

        d = {}
        for param, value in parsed:
            if isinstance(param, click.Parameter):
                value = str(value)
            if param.multiple or param.nargs != 1:
                d.setdefault(param, []).append(value)
            else:
                d[param] = value

        out = {
            param.name: param.process_value(click.Context(command), v)
            if isinstance(param, click.Parameter)
            else v
            for param, v in d.items()
        }
        return command, out

    def make_command_method(self, command):

        return (name_rule(command), lambda parsed: self.process_params(command, parsed))

    def make_group_method(self, group):
        def method(parsed):

            _group, out = self.process_params(
                group, [(obj, value) for obj, value in parsed]
            )
            return (group, out)

        return name_rule(group), method

    def make_param_method(self, cmd, param):
        @lark.v_args(inline=True)
        def method(parsed):

            return (param, parsed)

        return name_rule(param), method


@attr.dataclass
class Parser:
    group: CountingGroup
    callback: t.Callable = lambda **kw: kw
    _id_to_object: t.Dict[str, object] = attr.ib(factory=dict)

    def __attrs_post_init__(self):
        for cmd in get_base_commands(self.group):
            self._id_to_object[name_rule(cmd)] = cmd
            for param in cmd.params:
                self._id_to_object[name_rule(param)] = param

    def parse_string(self, s):
        grammar = build_grammar(self.group)

        parser = lark.Lark(grammar)
        tree = parser.parse(s)
        if not ALWAYS_ACCEPT:
            Walker(group=self.group).visit(tree)
        _group, value = Transformer(group=self.group).transform(tree)
        return value

    def parse_args(self, args: t.List[str]):
        line = subprocess.list2cmdline(args)
        return self.parse_string(line)

    def invoke_string(self, line: str):
        return self.callback(self.parse_string(line))

    def invoke_args(self, args: t.List[str]):
        return self.callback(self.parse_args(args))


if __name__ == "__main__":

    commands = [
        CountingCommand(
            name="dog",
            params=[
                click.Option(["--dog-name"]),
                click.Option(["--dog-color"], multiple=True),
                click.Option(["--age"], type=int, default=9),
            ],
            callback=lambda **kw: kw,
            nargs=-1,
        ),
        CountingGroup(
            name="cat",
            params=[click.Option(["--cat-name"])],
            commands={
                c.name: c
                for c in [
                    CountingCommand(
                        "owner",
                        params=[click.Option(["--owner-name"])],
                        callback=lambda **kw: kw,
                    )
                ]
            },
            callback=(lambda **kw: kw),
        ),
    ]
    grp = CountingGroup(
        name="top", commands={c.name: c for c in commands}, callback=(lambda **kw: kw)
    )

    tree = Parser(grp).parse_string(
        "top dog --dog-name fido --dog-color brown --dog-color black --age 21 cat --cat-name felix owner --owner-name Alice dog --dog-name spot --dog-color white "
    )
    print(tree)