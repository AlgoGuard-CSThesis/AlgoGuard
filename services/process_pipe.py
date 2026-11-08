"""Length-prefixed pickles over a child process's own stdin/stdout pipes.

Used by the live inference and capture processes (DEF-17). Only the two processes that created a
pipe can read it, so the pickles never come from anyone else.
"""

import pickle
import struct

_HEADER = struct.Struct("!Q")
PIPE_ERRORS = (OSError, EOFError, pickle.PickleError)


def send(stream, value):
    data = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_HEADER.pack(len(data)))
    stream.write(data)
    stream.flush()


def receive(stream):
    header = stream.read(_HEADER.size)
    if len(header) < _HEADER.size:
        raise EOFError("The other process closed the pipe.")
    (size,) = _HEADER.unpack(header)
    data = stream.read(size)
    if len(data) < size:
        raise EOFError("The other process closed the pipe.")
    # Only ever data from the process pair that created this pipe.
    return pickle.loads(data)
