"""This installation's identity (its node ID), created once and kept in the state folder."""

import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4


def local_node_id(path: Path) -> UUID:
    """Publish a complete UUID once; concurrent startups never replace it.

    The caller supplies its owner-restricted local state directory. A corrupt
    existing identity is an error, never a reason to silently enroll a new node.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        descriptor, temporary = tempfile.mkstemp(prefix=".node-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as output:
                output.write(str(uuid4()) + "\n")
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    return UUID(path.read_text(encoding="ascii").strip())
