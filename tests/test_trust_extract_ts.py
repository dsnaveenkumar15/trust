from trust.extract_ts import extract_ts

TS = r'''import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
const server = new McpServer({ name: "demo", version: "1.0.0" });

server.tool("get_weather", "Get the weather. " + "Fast.", { city: z.string() }, async () => ({}));

server.registerTool(
  "add",
  {
    description: `Adds two numbers.
  Multi-line.`,
    inputSchema: { a: z.number() },
  },
  async () => ({})
);

server.tool("fmt", "Formats​text.", {}, h);

const tools = [
  { name: "lookup", description: "Look things up.", inputSchema: { type: "object" } },
  { name: "not_a_tool", description: "No schema here." },
];
'''


def test_typescript_tool_shapes():
    tools = {t.name: t for t in extract_ts("src/index.ts", TS)}
    assert set(tools) == {"get_weather", "add", "fmt", "lookup"}
    assert tools["get_weather"].description == "Get the weather. Fast."
    assert tools["get_weather"].line == 4 and tools["get_weather"].language == "typescript"
    assert tools["add"].description == "Adds two numbers.\n  Multi-line."
    assert tools["add"].line == 6
    assert tools["fmt"].description == "Formats​text."      # JS escape decoded to the real character
    assert tools["lookup"].description == "Look things up."


def test_javascript_and_tsx_and_unknown_extensions():
    js = 'server.tool("ping", "Ping.", {}, h);\n'
    assert [t.language for t in extract_ts("index.mjs", js)] == ["javascript"]
    assert [t.name for t in extract_ts("ui.tsx", js)] == ["ping"]
    assert extract_ts("main.go", js) == []


def test_member_calls_without_string_name_are_ignored():
    assert extract_ts("a.ts", "server.tool(name, desc, {}, h);\nfoo.bar('x', 'y');\n") == []


def test_thousands_of_tools_extract_without_crashing_and_lines_stay_exact():
    src = "".join(f'server.tool("t{i}", "d", {{}}, h);\n' for i in range(3000))
    tools = extract_ts("big.ts", src)
    assert len(tools) == 3000 and tools[0].line == 1 and tools[-1].line == 3000

BS = "\\"


def test_template_substitutions_escapes_and_constants_are_resolved():
    src = ('const PREFIX = "<IMPOR";\n'
           'server.tool("a", `${PREFIX}${"T"}ANT> x`, {}, h);\n'
           'server.tool("b", "<IMPORT' + BS + 'ANT> y", {}, h);\n'
           'server.tool("c", "line' + BS + '\none", {}, h);\n')
    tools = {t.name: t for t in extract_ts("a.ts", src)}
    assert tools["a"].description == "<IMPORTANT> x" and not tools["a"].opaque
    assert tools["b"].description == "<IMPORTANT> y"
    assert tools["c"].description == "lineone"


def test_non_literal_description_is_marked_opaque_but_keeps_known_parts():
    src = 'server.tool("a", loadDescription(), {}, h);\nserver.tool("b", `x ${dynamic}`, {}, h);\n'
    assert [(t.name, t.opaque, t.description) for t in extract_ts("a.ts", src)] == [("a", True, ""), ("b", True, "x ")]


def test_out_of_range_and_surrogate_escapes_are_kept_verbatim():
    raw = "x" + BS + "u{110000}y" + BS + "uD800z"
    tool = extract_ts("a.ts", 'server.tool("a", "' + raw + '", {}, h);\n')[0]
    assert tool.description == raw


def test_double_backslash_is_not_decoded_twice():
    tool = extract_ts("a.ts", 'server.tool("a", "' + BS + BS + 'u0049", {}, h);\n')[0]
    assert tool.description == BS + "u0049"
