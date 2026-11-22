"""Cross-process single-writer lock (fcntl.flock). Fail closed; released by the OS on process death."""
import fcntl, os
from pathlib import Path
from ph6_brains_2208 import Rejected

class WriterLock:
    def __init__(self, root):
        Path(root).mkdir(parents=True, exist_ok=True)
        self.path = Path(root) / "writer.lock"
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd); self.fd = None
            raise Rejected("WRITER_LOCKED")
        os.ftruncate(self.fd, 0); os.write(self.fd, ("pid=%d\n" % os.getpid()).encode())   # informational only
    def release(self):
        if self.fd is not None:
            try: fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally: os.close(self.fd); self.fd = None
    def __enter__(self): return self
    def __exit__(self, *a): self.release()
