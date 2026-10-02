"""TS/JS MCP tool extraction with tree-sitter. Source is parsed, never run."""
import re
from bisect import bisect_left

import tree_sitter as ts
import tree_sitter_javascript as tsj
import tree_sitter_typescript as tst

from trust.models import ToolDef

_LANGS = {
    ".ts": ("typescript", tst.language_typescript),
    ".mts": ("typescript", tst.language_typescript),
    ".cts": ("typescript", tst.language_typescript),
    ".tsx": ("typescript", tst.language_tsx),
    ".js": ("javascript", tsj.language),
    ".mjs": ("javascript", tsj.language),
    ".cjs": ("javascript", tsj.language),
    ".jsx": ("javascript", tsj.language),
}
TS_EXTENSIONS = tuple(_LANGS)
_PARSERS: dict[str, ts.Parser] = {}
_CODEPOINT = re.compile(r"u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})")
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
TOOL_METHODS = ("tool", "registerTool")


def _parser(ext: str) -> ts.Parser:
    if ext not in _PARSERS:
        _PARSERS[ext] = ts.Parser(ts.Language(_LANGS[ext][1]()))
    return _PARSERS[ext]


def _decode_escape(seq: str) -> str:
    """One JS escape_sequence token, decoded the way the JS engine would. Unrepresentable code points stay verbatim."""
    body = seq[1:]
    if not body or body[0] in "\r\n  ":
        return ""                                   # line continuation
    if body[0] in "ux":
        match = _CODEPOINT.fullmatch(body)
        if match:
            code = int(next(g for g in match.groups() if g), 16)
            if code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF:
                return chr(code)
        return seq
    return _SIMPLE_ESCAPES.get(body, body)


def _piece(node) -> str:
    text = node.text.decode("utf-8", "replace")
    return _decode_escape(text) if node.type == "escape_sequence" else text


def _string_value(node, consts: dict) -> tuple[str, bool] | None:
    """(text, opaque) for a string-valued expression; None when nothing literal is known about it."""
    if node is None:
        return None
    kind = node.type
    if kind == "string":
        return "".join(_piece(c) for c in node.children if c.type in ("string_fragment", "escape_sequence")), False
    if kind == "template_string":
        parts, opaque = [], False
        for child in node.children:
            if child.type in ("string_fragment", "escape_sequence"):
                parts.append(_piece(child))
            elif child.type == "template_substitution":
                inner = _string_value(child.named_children[0] if child.named_children else None, consts)
                if inner is None:
                    opaque = True
                else:
                    parts.append(inner[0])
                    opaque = opaque or inner[1]
        return "".join(parts), opaque
    if kind == "identifier":
        return consts.get(node.text.decode("utf-8", "replace"))
    if kind == "binary_expression":
        left = _string_value(node.child_by_field_name("left"), consts)
        right = _string_value(node.child_by_field_name("right"), consts)
        if left is None and right is None:
            return None
        return (left or ("", True))[0] + (right or ("", True))[0], left is None or right is None or left[1] or right[1]
    if kind == "parenthesized_expression" and node.named_children:
        return _string_value(node.named_children[0], consts)
    return None


def _pair_value(obj, key: str):
    for child in obj.named_children:
        if child.type == "pair":
            key_node = child.child_by_field_name("key")
            if key_node is not None and key_node.text.decode("utf-8", "replace").strip("'\"`") == key:
                return child.child_by_field_name("value")
    return None


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _description(node, consts: dict) -> tuple[str, bool]:
    value = _string_value(node, consts)
    return value if value is not None else ("", True)


def _tool_call(node, path: str, language: str, line: int, consts: dict) -> ToolDef | None:
    func, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
    if func is None or args is None or func.type != "member_expression":
        return None
    prop = func.child_by_field_name("property")
    if prop is None or prop.text.decode("utf-8", "replace") not in TOOL_METHODS:
        return None
    argv = args.named_children
    name = _string_value(argv[0], consts) if argv else None
    if not name or not name[0]:
        return None
    description, opaque = "", False
    if len(argv) > 1:
        second = argv[1]
        if second.type == "object":
            desc_node = _pair_value(second, "description")
            if desc_node is not None:
                description, opaque = _description(desc_node, consts)
        else:
            description, opaque = _description(second, consts)
    return ToolDef(name[0], description, path, line, language, opaque)


def extract_ts(path: str, text: str) -> list[ToolDef]:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in _LANGS:
        return []
    language = _LANGS[ext][0]
    data = text.encode("utf-8", "surrogatepass")
    newlines = [m.start() for m in re.finditer(b"\n", data)]
    root = _parser(ext).parse(data).root_node
    consts: dict[str, tuple[str, bool]] = {}
    found: dict[tuple[str, int], ToolDef] = {}
    for node in _walk(root):   # source order, so a constant is known before later uses
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value = _string_value(node.child_by_field_name("value"), consts)
            if name_node is not None and name_node.type == "identifier" and value is not None:
                consts[name_node.text.decode("utf-8", "replace")] = value
            continue
        # Line from the byte offset: Node.start_point segfaults after many reads in tree-sitter 0.26.0.
        line = bisect_left(newlines, node.start_byte) + 1
        tool = None
        if node.type == "call_expression":
            tool = _tool_call(node, path, language, line, consts)
        elif node.type == "object" and _pair_value(node, "inputSchema") is not None:
            name = _string_value(_pair_value(node, "name"), consts)
            desc_node = _pair_value(node, "description")
            if name and name[0] and desc_node is not None:
                description, opaque = _description(desc_node, consts)
                tool = ToolDef(name[0], description, path, line, language, opaque)
        if tool is not None:
            found.setdefault((tool.name, tool.line), tool)
    return sorted(found.values(), key=lambda t: (t.line, t.name))
