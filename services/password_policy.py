"""Password rule shared by local (SQLite) and cloud account creation."""

import re

PASSWORD_RULE = re.compile(r"(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,128}", re.S)
PASSWORD_RULE_TEXT = (
    "Passwords need 12 to 128 characters with upper- and lower-case letters, a digit, "
    "and a symbol."
)


def password_is_strong(password):
    return isinstance(password, str) and PASSWORD_RULE.fullmatch(password) is not None
