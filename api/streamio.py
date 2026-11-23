# datview/api/streamio.py
# Random-access reading of CrystFEL stream chunks using the byte offsets stored by datgen.py,
# and writing a selection either to a stream file or straight into process_hkl / partialator.

import os
import shlex
import subprocess
from collections import OrderedDict

CHUNK_START = b"----- Begin chunk -----\n"
CHUNK_END = b"----- End chunk -----\n"
CRYSTAL_START = b"--- Begin crystal\n"


# ---------------------------------------------------------------------------
# Chunk reader
# ---------------------------------------------------------------------------
class StreamChunkReader:
    """Reads chunks from stream files by byte offset.

    Files are opened in binary mode so offsets are exact byte positions regardless of
    platform or text encoding. A small pool of open handles avoids reopening the same
    stream for every chunk when a selection alternates between files.
    """

    def __init__(self, max_open=16):
        self.max_open = max_open
        self.handles = OrderedDict()

    def _handle(self, path):
        fh = self.handles.pop(path, None)
        if fh is None:
            fh = open(path, "rb")
            if len(self.handles) >= self.max_open:
                _, old = self.handles.popitem(last=False)
                old.close()
        self.handles[path] = fh
        return fh

    def header(self, path):
        """Everything before the first chunk: format line, command line, geometry, cell."""
        fh = self._handle(path)
        fh.seek(0)
        out = []
        for line in fh:
            if line == CHUNK_START:
                break
            out.append(line)
        return b"".join(out)

    def read(self, path, start, end):
        fh = self._handle(path)
        fh.seek(start)
        return fh.read(end - start)

    def chunk(self, path, istart, iend, cstart=-1, cend=-1):
        """One chunk, reduced to a single crystal when cstart/cend are given.

        Multi-crystal chunks are split so each exported chunk holds exactly one crystal:
        the chunk header (everything before the first crystal block, peaks included),
        the requested crystal block, then the end-of-chunk marker.
        """
        data = self.read(path, istart, iend)
        if cstart < 0 or cend < 0:
            return data
        first = data.find(CRYSTAL_START)
        if first < 0:
            return data
        return data[:first] + data[cstart - istart:cend - istart] + CHUNK_END

    def close(self):
        for fh in self.handles.values():
            fh.close()
        self.handles.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------------------
# Selection output
# ---------------------------------------------------------------------------
def iter_selection(rows, header=True):
    """Yield the bytes of a stream made from `rows`.

    `rows` is an iterable of (stream_path, istart, iend, cstart, cend). The header of the
    first stream file is written once, as in the original DatView stream export.
    """
    with StreamChunkReader() as reader:
        need_header = header
        for path, istart, iend, cstart, cend in rows:
            if need_header:
                yield reader.header(path)
                need_header = False
            yield reader.chunk(path, int(istart), int(iend), int(cstart), int(cend))


def write_selection(rows, fname):
    n = 0
    with open(fname, "wb") as out:
        for i, block in enumerate(iter_selection(rows)):
            out.write(block)
            n = i
    return n  # number of chunks written (header excluded)


def merge_command(cmd):
    """Turn a user command string into an argv list that reads the stream from stdin.

    Accepts e.g. "process_hkl -y 2/m -o out.hkl" or "partialator -y 2/m -o out.hkl
    --model=unity". An explicit -i/--input is replaced with stdin.
    """
    argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    cleaned = []
    skip = False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a in ("-i", "--input"):
            skip = True
            continue
        if a.startswith("--input=") or (a.startswith("-i") and not a.startswith("--") and len(a) > 2):
            continue
        cleaned.append(a)
    return cleaned + ["-i", "-"]


def pipe_selection(rows, cmd, progress=None, cwd=None):
    """Stream the selection into a CrystFEL merging program via stdin.

    No intermediate stream file is written. `progress(n)` is called every 200 chunks and
    may return False to abort. Returns the program's exit code, or None if aborted.
    """
    argv = merge_command(cmd)
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, cwd=cwd)
    aborted = False
    try:
        for n, block in enumerate(iter_selection(rows)):
            proc.stdin.write(block)
            if progress is not None and n % 200 == 0 and progress(n) is False:
                aborted = True
                break
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
    if aborted:
        proc.terminate()
        proc.wait()
        return None
    return proc.wait()


def resolve_output_name(cmd, suffix):
    """Insert a partition suffix before the extension of the -o/--output argument."""
    argv = shlex.split(cmd)
    for i, a in enumerate(argv):
        if a in ("-o", "--output") and i + 1 < len(argv):
            argv[i + 1] = _suffixed(argv[i + 1], suffix)
        elif a.startswith("--output="):
            argv[i] = "--output=" + _suffixed(a[len("--output="):], suffix)
    return shlex.join(argv)


def _suffixed(name, suffix):
    root, ext = os.path.splitext(name)
    return "%s_%s%s" % (root, suffix, ext)
