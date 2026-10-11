"""XPath string escaping utilities."""


def escape_xpath_string(value: str) -> str:
    """Return an XPath string literal that safely encodes value.

    XPath has no escape character, so strings containing both ' and "
    must be built with concat().

    Examples:
        escape_xpath_string("hello")      -> "'hello'"
        escape_xpath_string("O'Brien")    -> '"O\'Brien"'  (uses double quotes)
        escape_xpath_string('say "hi"')   -> "'say \"hi\"'"
        escape_xpath_string("it's a \"test\"") -> "concat('it', \"'\", 's a \"test\"')"
    """
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    # Contains both quote types: use XPath concat()
    parts = value.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"
