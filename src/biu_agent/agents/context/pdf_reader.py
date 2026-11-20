"""Extract text from a short PDF in a time-limited helper process using stdin/stdout."""

import io
import sys

from pypdf import PdfReader


def main():
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (5, 6))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read()), strict=True)
    if reader.is_encrypted or len(reader.pages) > 64:
        raise ValueError
    parts = []
    remaining = 64000
    for page in reader.pages:
        value = page.extract_text() or ""
        remaining -= len(value)
        if remaining < 0:
            raise ValueError
        parts.append(value)
    sys.stdout.buffer.write("\n\n".join(parts).encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
