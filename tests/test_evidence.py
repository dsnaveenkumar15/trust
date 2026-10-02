from core.evidence import missing, verify_quote

SRC = "import os\r\n\r\ndef run(cmd):\r\n\treturn os.system(cmd)\r\n\r\n# end\r\n"
FILES = {"server.py": SRC}


def test_exact_quote_on_cited_line():
    assert verify_quote(FILES, "server.py", 4, "return os.system(cmd)")


def test_crlf_and_tabs_are_normalised():
    assert verify_quote(FILES, "server.py", 4, "  return   os.system(cmd)  ")


def test_quote_spanning_two_lines():
    assert verify_quote(FILES, "server.py", 3, "def run(cmd): return os.system(cmd)")


def test_quote_within_window_of_cited_line():
    assert verify_quote(FILES, "server.py", 6, "os.system(cmd)")  # line 4 is within ±3


def test_quote_outside_window_rejected():
    files = {"a.py": "\n".join(["x = 1"] * 20 + ["secret = 'leak'"])}
    assert not verify_quote(files, "a.py", 1, "secret = 'leak'")


def test_altered_quote_rejected():
    assert not verify_quote(FILES, "server.py", 4, "return os.popen(cmd)")


def test_missing_path_and_bad_line_rejected():
    assert not verify_quote(FILES, "other.py", 1, "import os")
    assert not verify_quote(FILES, "server.py", 0, "import os")
    assert not verify_quote(FILES, "server.py", 99, "import os")


def test_trivially_short_quote_rejected():
    assert not verify_quote(FILES, "server.py", 1, "os")


def test_missing_preserves_order_and_dedupes():
    assert missing(["b", "x", "a", "x", "y"], {"a", "b"}) == ["x", "y"]
    assert missing([], {"a"}) == []


def test_quote_in_text_is_whitespace_insensitive_and_needs_a_real_quote():
    from core.evidence import quote_in_text
    text = "Breaking:   removed\n  foo().\nUse bar()."
    assert quote_in_text(text, "removed foo(). Use bar()") is True
    assert quote_in_text(text, "never said this") is False
    assert quote_in_text(text, "a") is False and quote_in_text("", "removed") is False
