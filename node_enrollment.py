"""Installation identity and explicit membership selection; not wired into Flask yet."""

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


def select_node(local_id: UUID, memberships: list[dict], requested: UUID | None = None) -> UUID:
    """The local installation is the default; another node needs an explicit choice.

    Membership data is a convenience for the UI, not an authorization boundary.
    PostgreSQL checks the current membership again for every operation.
    """
    selected = requested or local_id
    if not any(
        item.get("status") == "approved"
        and item.get("node_status") == "approved"
        and item.get("node_id") == str(selected)
        for item in memberships
    ):
        raise PermissionError("This node needs an approved membership.")
    return selected
