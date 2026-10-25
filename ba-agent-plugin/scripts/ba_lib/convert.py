"""`convert`: turn a PDF, DOC, RTF or XLSX BRD into Markdown with tools on this computer, so the model never has to read the
whole document and rewrite it by hand (that costs about twice the document size, mostly in output tokens).

The core stays standard-library only: XLSX is read from its XML directly; PDF and DOC/RTF use a converter that is detected at
run time (`pdftotext`, `pypdf`, LibreOffice `soffice`). When none exists the error is `CONVERTER_UNAVAILABLE` and only then
does the caller transcribe by hand. Requirement quotes are still checked against the converted copy, so a bad conversion is
rejected, not hidden.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import ingest
from .util import BAError, normalize_ws

TEXT_EXT = {".pdf", ".doc", ".rtf", ".xlsx"}
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)


def _promote_headings(text: str) -> str:
    """Plain text has no heading markers: lines such as `2.1 Borrowing limits` become Markdown headings, so the core can
    outline the document. Only numbered, short lines without sentence punctuation are promoted (same rule as `.txt`)."""
    out = []
    for ln in text.split("\n"):
        m = ingest._NUM_HEAD.match(ln.strip())
        if m and not m.group(2).rstrip().endswith((".", ";", ",", ":")):
            out.append("#" * min(m.group(1).count(".") + 1, 6) + " " + ln.strip())
        else:
            out.append(ln.strip() if ln.strip() else "")
    return "\n".join(out)


def _pdf(src: Path) -> tuple[str, str, list[str]]:
    warn = ["PDF layout is lost: tables arrive as aligned text, and headings are inferred from their numbering. Check "
            "important tables against the original."]
    exe = shutil.which("pdftotext")
    if exe:
        r = _run([exe, "-layout", "-enc", "UTF-8", str(src), "-"])
        if r.returncode == 0 and r.stdout.strip():
            return "pdftotext", _promote_headings(r.stdout.decode("utf-8", "replace").replace("\f", "\n\n")), warn
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(src))
        pages = [(pg.extract_text() or "") for pg in reader.pages]
        if any(p.strip() for p in pages):
            return "pypdf", _promote_headings("\n\n".join(pages)), warn
    except ImportError:
        pass
    except Exception as e:  # corrupt or encrypted file: fall through to the unavailable error with the reason
        raise BAError("UNREADABLE_SOURCE", f"The PDF could not be read: {type(e).__name__}: {e}")
    raise BAError("CONVERTER_UNAVAILABLE", "No PDF converter was usable here (looked for `pdftotext` and the `pypdf` module; a "
                  "scanned PDF with no text layer also gives nothing). Read the PDF yourself with your own file tools and write "
                  "its text faithfully to source/<same name>.md.")


def _office(src: Path) -> tuple[str, str, list[str]]:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise BAError("CONVERTER_UNAVAILABLE", f"No {src.suffix} converter was usable here (looked for LibreOffice `soffice`). "
                      "Read the file yourself with your own file tools and write its text faithfully to source/<same name>.md.")
    failed = BAError("CONVERTER_UNAVAILABLE", f"LibreOffice could not convert {src.name}. Read the file yourself and write "
                     "its text faithfully to source/<same name>.md.")
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as profile:
        # A private profile, so a LibreOffice window the user already has open cannot block the headless run.
        cmd = [exe, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "docx", "--outdir", tmp, str(src)]
        try:
            r = _run(cmd)
        except (subprocess.TimeoutExpired, OSError):
            raise failed
        out = Path(tmp) / (src.stem + ".docx")
        if r.returncode != 0 or not out.exists():
            raise failed
        text, warnings = ingest._docx_text(out)
    if not re.search(r"(?m)^#{1,6} ", text):  # an old .doc / .rtf often has numbered lines but no heading styles
        text = _promote_headings(text)
    return "soffice+docx", text, warnings


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx(src: Path) -> tuple[str, str, list[str]]:
    warn = ["Formulas are read as the values last saved in the file; merged cells appear once; images and charts are not read."]
    try:
        with zipfile.ZipFile(src) as z:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(RELS + "Relationship")}
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(M + "si"):
                    shared.append("".join(t.text or "" for t in si.iter(M + "t")))
            parts = []
            for sh in wb.iter(M + "sheet"):
                target = rels.get(sh.get(R + "id"), "")
                path = target.lstrip("/") if target.startswith("/") else "xl/" + target
                if path not in z.namelist():
                    continue
                rows = []
                for row in ET.fromstring(z.read(path)).iter(M + "row"):
                    cells = {}
                    for c in row.iter(M + "c"):
                        v, t = c.find(M + "v"), c.get("t")
                        if t == "inlineStr":
                            val = "".join(x.text or "" for x in c.iter(M + "t"))
                        elif v is None or v.text is None:
                            continue
                        elif t == "s":
                            val = shared[int(v.text)]
                        elif t == "b":
                            val = "TRUE" if v.text == "1" else "FALSE"
                        else:
                            val = v.text
                        cells[_col(c.get("r"))] = normalize_ws(val).replace("|", "/")
                    if cells:
                        rows.append(cells)
                if not rows:
                    continue
                width = max(max(r) for r in rows) + 1
                grid = [[r.get(i, "") for i in range(width)] for r in rows]
                parts.append(f"## {sh.get('name')}\n\n| " + " | ".join(grid[0]) + " |\n|" + "---|" * width + "\n"
                             + "\n".join("| " + " | ".join(g) + " |" for g in grid[1:]))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise BAError("UNREADABLE_SOURCE", f"The workbook could not be read: {type(e).__name__}: {e}")
    if not parts:
        raise BAError("EMPTY_SOURCE", "The workbook has no cell values.")
    return "xlsx", "# " + src.stem + "\n\n" + "\n\n".join(parts), warn


def convert(root: str | None, src: str, out: str | None = None) -> dict:
    """Write `<root>/source/<name>.md` (or `out`, which must stay inside `<root>/source/`) and report how."""
    if not root:
        raise BAError("NO_PROJECT", "convert needs --root <output folder> (the folder the user confirmed for outputs).")
    inp, base = Path(src), Path(root).resolve()  # converters get an absolute path below, so a name starting with `-` is not an option
    if not inp.is_file():
        raise BAError("SOURCE_NOT_FOUND", f"{inp} not found")
    ext = inp.suffix.lower()
    if ext not in TEXT_EXT:
        raise BAError("UNSUPPORTED_FORMAT", f"`convert` handles {', '.join(sorted(TEXT_EXT))}; {ext or 'this file'} is read directly by `init` "
                      "(.md, .txt, .docx).", extension=ext)
    dest = (Path(out) if out and Path(out).is_absolute() else base / (out or f"source/{inp.stem}.md")).resolve()
    if base / "source" not in dest.parents or dest.suffix.lower() != ".md":
        raise BAError("BAD_INPUT", "--out must be a .md file inside <root>/source/.")
    inp = inp.resolve()
    try:
        method, text, warnings = {".pdf": _pdf, ".xlsx": _xlsx}.get(ext, _office)(inp)
    except subprocess.TimeoutExpired:
        raise BAError("CONVERTER_UNAVAILABLE", f"The converter for {ext} did not finish in time. Read the file yourself with your own "
                      "file tools and write its text faithfully to source/<same name>.md.")
    text = ingest.normalize_text(text)
    if len(text.strip()) < 20:
        raise BAError("EMPTY_SOURCE", "The converted document has (almost) no text; it may be a scan. Read it yourself and "
                      "write its text faithfully to source/<same name>.md.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    return {"method": method, "source": str(inp.resolve()), "out": str(dest), "chars": len(text),
            "headings": len(re.findall(r"(?m)^#{1,6} ", text)), "warnings": warnings,
            "next": f"Run `init --brd \"{dest}\"`. Tell the user the conversion method and any warning in one line."}
