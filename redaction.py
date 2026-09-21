"""Secret redaction shared by application and maintainer tooling.

Stage 5A.2 requires that logs redact tokens, credentials, and signed URLs.
This lives in its own module, separate from `config.py`, so maintainer tools
do not depend on the analyst configuration loader just to redact messages.
Both sides import the helper independently, keeping their credential sources
separate.

`config.py` re-exports `redact_for_logging` so existing call sites keep
working.
"""
from __future__ import annotations

import re

_REDACT_KEYWORDS = ("key", "secret", "password", "token", "signature", "credential")

# postgresql://user:PASSWORD@host -> postgresql://user:***@host
#
# The password segment allows "@" so that an unencoded
# "@" inside the password does not end the match early and leak its tail:
# `//u:p@ssw0rd@host/db` must redact `p@ssw0rd`, not just `p`. Greedy
# matching then backtracks to the last "@" before the path, which is where
# userinfo ends in a URL. URL and log-message delimiters bound the match so
# unrelated text or a later email address cannot be swallowed.
_CREDENTIAL_IN_URL = re.compile(r"(://[^:/@\s\"'<>?#]+:)[^/\s<>?#]*(@)")

# ?token=... / &X-Api-Key=... / &access_token=... -> value replaced
#
# The keyword may appear ANYWHERE in the parameter name, not just at its
# start. Anchoring to the start missed `apikey=` and `access_token=` —
# the two spellings Supabase and most REST APIs actually use.
_SENSITIVE_QUERY_PARAM = re.compile(
    r"([?&](?:[^=&\s\"'<>#]*(?:"
    + "|".join(_REDACT_KEYWORDS)
    + r")[^=&\s\"'<>#]*|sig)=)[^&\s\"'<>#]*",
    flags=re.IGNORECASE,
)

# libpq supports quoted values and backslash escapes, including escaped
# whitespace in an unquoted password. Consume the entire value so an escaped
# quote or space cannot leave the remaining part of the secret in the log.
# Excluding query delimiters keeps ordinary parameters following ?password=
# intact; the URL query rule above already handles that spelling.
_KEYWORD_PASSWORD = re.compile(
    r"(?<![\w?&-])(?P<prefix>password\s*=\s*)"
    r"(?P<value>'(?:\\[\s\S]|[^'\\])*'?|\"(?:\\[\s\S]|[^\"\\])*\"?"
    r"|(?:\\[\s\S]|[^\s\\])+)",
    flags=re.IGNORECASE,
)

# Match both wire headers and logged mappings, such as
# {'Authorization': 'Bearer ...'}. Preserve the scheme and surrounding quotes
# so that non-sensitive headers and other diagnostic context remain readable.
_AUTHORIZATION = re.compile(
    r"(\b(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?\s*(?:bearer|basic)\s+)"
    r"[^\s\"',;<>\[\]{}]+",
    flags=re.IGNORECASE,
)


def _redact_keyword_password(match: re.Match[str]) -> str:
    value = match.group("value")
    quote = value[0] if value[0] in "\"'" else ""
    closing_quote = quote if len(value) > 1 and value.endswith(quote) else ""
    return match.group("prefix") + quote + "***" + closing_quote


def redact_for_logging(text: str) -> str:
    """Best-effort redaction of secrets, tokens, and signed URL query
    parameters before writing a string to any log or console.

    Not a substitute for simply not logging privileged values in the first
    place — this is a defense-in-depth backstop for maintainer tooling
    (5B/5C/5D), where connection strings and signed download URLs are more
    likely to surface inside exception messages.
    """
    text = _CREDENTIAL_IN_URL.sub(r"\1***\2", str(text))
    text = _SENSITIVE_QUERY_PARAM.sub(r"\1***", text)
    text = _KEYWORD_PASSWORD.sub(_redact_keyword_password, text)
    return _AUTHORIZATION.sub(r"\1***", text)
