"""Multi-dataset ("Multi") support shared by the joint fit and the IQuana export.

A multi-fit request carries one model for every member dataset plus, per member,
its own values, polydispersity, slider ranges (= fit bounds) and Q window:

    {"model": ..., "structure_factor": ..., "method": ..., "controls": {...},
     "members": [{"id", "alias", "params", "pd", "free": {name: [lo, hi]}, "window"}, ...],
     "shared": ["radius", "radius_pd", ...],          # one value for all members
     "constraints": {"d2o.scale": "0.8 * h2o.scale"}} # per-member expressions

Expressions follow IQuana's rules: numbers, ``alias.param`` references (a bare
``param`` means the same dataset), ``+ - * /``, parentheses and integer powers
(``**`` or ``^``). They are parsed into a small AST and interpreted, never executed.
"""
from __future__ import annotations

import ast
import keyword
import re

from .backend import UserError

ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def check_alias(alias: str) -> str:
    if not (isinstance(alias, str) and ALIAS_RE.match(alias)) or keyword.iskeyword(alias):
        raise UserError(f"Dataset name {alias!r} must start with a letter and contain only letters, digits and _.")
    return alias


def parse(expr: str, own: str, known: dict[str, set[str]]) -> tuple[ast.AST, list[tuple[str, str]]]:
    """Validate ``expr`` for dataset ``own``. ``known`` maps alias -> parameter names.
    Returns the (rewritten, fully qualified) AST and the referenced (alias, param) pairs."""
    text = str(expr).strip().replace("^", "**")
    if not text:
        raise UserError("Empty expression.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise UserError(f"Cannot read expression {expr!r}.") from None
    refs: list[tuple[str, str]] = []

    def ref(alias, name):
        if alias not in known:
            raise UserError(f"{expr!r}: unknown dataset {alias!r} (datasets: {', '.join(known)}).")
        if name not in known[alias]:
            raise UserError(f"{expr!r}: {alias} has no parameter {name!r}.")
        refs.append((alias, name))
        return ast.Attribute(value=ast.Name(id=alias, ctx=ast.Load()), attr=name, ctx=ast.Load())

    def walk(n):
        if isinstance(n, ast.Expression):
            return ast.Expression(body=walk(n.body))
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n
        if isinstance(n, ast.Name):
            return ref(own, n.id)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            return ref(n.value.id, n.attr)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return ast.UnaryOp(op=n.op, operand=walk(n.operand))
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            if isinstance(n.op, ast.Pow):
                e = n.right
                neg = isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub)
                e = e.operand if neg else e
                if not (isinstance(e, ast.Constant) and isinstance(e.value, int) and not isinstance(e.value, bool)):
                    raise UserError(f"{expr!r}: only integer powers are allowed.")
            return ast.BinOp(left=walk(n.left), op=n.op, right=walk(n.right))
        raise UserError(f"{expr!r}: only numbers, dataset.parameter references, + - * /, "
                        "parentheses and integer powers are allowed.")

    out = ast.fix_missing_locations(walk(tree))
    return out, refs


def canonical(expr: str, own: str, known: dict[str, set[str]]) -> str:
    """The expression with every reference qualified, as IQuana expects."""
    tree, _ = parse(expr, own, known)
    return ast.unparse(tree.body)


def interpret(tree: ast.AST, lookup):
    """Evaluate a parsed expression; ``lookup(alias, name)`` supplies values (numbers
    or bumps Parameters, whose arithmetic builds a bumps Expression)."""
    n = tree.body if isinstance(tree, ast.Expression) else tree
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, ast.Attribute):
        return lookup(n.value.id, n.attr)
    if isinstance(n, ast.UnaryOp):
        v = interpret(n.operand, lookup)
        return -v if isinstance(n.op, ast.USub) else v
    a, b = interpret(n.left, lookup), interpret(n.right, lookup)
    if isinstance(n.op, ast.Add):
        return a + b
    if isinstance(n.op, ast.Sub):
        return a - b
    if isinstance(n.op, ast.Mult):
        return a * b
    if isinstance(n.op, ast.Div):
        return a / b
    return a ** b


def check_request(req: dict):
    """Validate the multi part of a request; returns (members, shared, constraints as
    {(alias, name): (tree, canonical)}) in dependency order (no cycles)."""
    members = req.get("members") or []
    if len(members) < 2:
        raise UserError("Multi needs at least two ticked datasets.")
    aliases = [check_alias(m.get("alias")) for m in members]
    if len(set(aliases)) != len(aliases):
        raise UserError("Dataset names in a multi-fit must be unique.")
    known = {m["alias"]: set((m.get("params") or {}).keys()) | {f"{k}_pd" for k in (m.get("pd") or {})}
             for m in members}
    shared = list(dict.fromkeys(req.get("shared") or []))
    cons = {}
    for target, expr in (req.get("constraints") or {}).items():
        alias, _, name = str(target).partition(".")
        if alias not in known or name not in known[alias]:
            raise UserError(f"Constraint target {target!r} is not a parameter of a ticked dataset.")
        if name in shared:
            raise UserError(f"{name} is shared between datasets, so it cannot also have an expression.")
        tree, refs = parse(expr, alias, known)
        if (alias, name) in refs:
            raise UserError(f"{target} = {expr}: an expression cannot refer to itself.")
        cons[(alias, name)] = (tree, ast.unparse(tree.body), refs)
    # order so every constraint comes after the constraints it reads; reject cycles
    order, state = [], {}

    def visit(k, path):
        if state.get(k) == 2:
            return
        if state.get(k) == 1:
            raise UserError("Circular expressions: " + " → ".join(f"{a}.{n}" for a, n in path + [k]))
        state[k] = 1
        for r in cons[k][2]:
            if r in cons:
                visit(r, path + [k])
        state[k] = 2
        order.append(k)

    for k in cons:
        visit(k, [])
    return members, shared, {k: cons[k][:2] for k in order}
