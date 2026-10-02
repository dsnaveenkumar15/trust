"""Python MCP tool extraction with the stdlib parser. Source is parsed, never imported or run."""
import ast
import inspect
import textwrap

from trust.models import ToolDef

_UNWRAPPERS = {"dedent": textwrap.dedent, "cleandoc": inspect.cleandoc}


def _value(node, consts: dict) -> tuple[str, bool] | None:
    """(text, opaque) for a string-valued expression; None when nothing literal is known about it."""
    if isinstance(node, ast.Constant):
        return (node.value, False) if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts, opaque = [], False
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            elif isinstance(piece, ast.FormattedValue):
                if isinstance(piece.value, ast.Constant):
                    parts.append(str(piece.value.value))
                else:
                    inner = _value(piece.value, consts)
                    if inner is None:
                        opaque = True
                    else:
                        parts.append(inner[0])
                        opaque = opaque or inner[1]
        return "".join(parts), opaque
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _value(node.left, consts), _value(node.right, consts)
        if left is None and right is None:
            return None
        return (left or ("", True))[0] + (right or ("", True))[0], left is None or right is None or left[1] or right[1]
    if isinstance(node, ast.Call) and len(node.args) == 1 and _callee_name(node.func) in _UNWRAPPERS:
        inner = _value(node.args[0], consts)
        if inner is not None:
            return _UNWRAPPERS[_callee_name(node.func)](inner[0]), inner[1]
    return None


def _kwarg(call: ast.Call | None, name: str):
    """The keyword's AST node, or None when the keyword is absent."""
    if call is None:
        return None
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _callee_name(node) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _module_constants(tree: ast.Module) -> dict:
    consts: dict[str, tuple[str, bool]] = {}
    for stmt in tree.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
        value = getattr(stmt, "value", None)
        if value is None:
            continue
        resolved = _value(value, consts)
        if resolved is not None:
            for target in targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = resolved
    return consts


def extract_python(path: str, text: str) -> list[ToolDef]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    consts = _module_constants(tree)
    tools: list[ToolDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                if _callee_name(call.func if call else decorator) != "tool":
                    continue
                name_value = _value(_kwarg(call, "name"), consts) if _kwarg(call, "name") is not None else None
                name = name_value[0] if name_value and name_value[0] else node.name
                desc_node = _kwarg(call, "description")
                if desc_node is not None:
                    description, opaque = _value(desc_node, consts) or ("", True)
                else:
                    description, opaque = ast.get_docstring(node) or "", False
                tools.append(ToolDef(name, description, path, decorator.lineno, "python", opaque))
        elif isinstance(node, ast.Call) and _callee_name(node.func) == "Tool":
            name_node = _kwarg(node, "name")
            name = _value(name_node, consts) if name_node is not None else None
            if name and name[0]:
                desc_node = _kwarg(node, "description")
                description, opaque = (_value(desc_node, consts) or ("", True)) if desc_node is not None else ("", False)
                tools.append(ToolDef(name[0], description, path, node.lineno, "python", opaque))
    return sorted(tools, key=lambda t: (t.line, t.name))
