import io
import zipfile
from typing import Iterable


def iter_json_files_from_dir_in_zip(zf: zipfile.ZipFile, dir_prefix: str) -> Iterable[tuple[str, io.IOBase]]:
    names = zf.namelist()
    base_prefix = dir_prefix.rstrip("/") + "/"
    roots = {n.split("/", 1)[0] for n in names if "/" in n}
    candidate_prefixes = [base_prefix]
    if len(roots) == 1:
        root = next(iter(roots))
        candidate_prefixes.append(f"{root}/{base_prefix}")

    seen = set()
    for name in names:
        if name.endswith("/") or not name.lower().endswith(".json"):
            continue
        if any(name.startswith(p) for p in candidate_prefixes):
            if name in seen:
                continue
            seen.add(name)
            with zf.open(name, "r") as fp:
                yield name, io.BytesIO(fp.read())

def open_top_level_file_from_zip(zf: zipfile.ZipFile, filename: str) -> io.IOBase:
    names = set(zf.namelist())

    def _open_to_io_base(n: str) -> io.IOBase:
        with zf.open(n, "r") as fp:
            return io.BytesIO(fp.read())

    if filename in names:
        return _open_to_io_base(filename)

    roots = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(roots) == 1:
        candidate = f"{next(iter(roots))}/{filename}"
        if candidate in names:
            return _open_to_io_base(candidate)

    raise FileNotFoundError(f"Missing required top-level file: {filename}")