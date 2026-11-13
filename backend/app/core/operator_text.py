"""Plain operator text validation shared by incident and recommendation workflows."""
import re

def clean_operator_text(value):
    if value is None:return None
    value=value.strip()
    if re.search(r'(?i)(password|token|secret|authorization|api[_ -]?key)\s*[:=]|-----BEGIN .*PRIVATE KEY',value):
        raise ValueError('Do not include credentials in technician notes')
    if any(ord(c)<32 and c not in '\n\t' for c in value):raise ValueError('Control characters not allowed')
    return value or None
