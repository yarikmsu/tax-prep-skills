#!/usr/bin/env python3
"""Shared PDF/Excel/CSV parsing utilities for tax document extraction.

Used by /tax-parse subagents to extract text and tables from source documents.
Wraps pdfplumber with error handling and provides consistent interfaces.
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from agents.lib.secure_fs import is_reparse_point, redact  # noqa: E402

# Activate defusedxml to protect openpyxl against XXE attacks in crafted .xlsx files.
# Must be called before any XML parsing (including openpyxl imports).
try:
    import defusedxml
    defusedxml.defuse_stdlib()
except ImportError:
    print(
        "WARNING: defusedxml not installed — .xlsx parsing is vulnerable to XXE attacks. "
        "Install with: pip install defusedxml",
        file=sys.stderr,
    )


MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB — prevents DoS via oversized files


def _validate_file_path(
    path: str,
    expected_suffixes: set[str] | None = None,
    workspace: str | None = None,
) -> Path:
    """Validate that a file path exists, check extension, and enforce containment.

    Checks reparse points on the original (unresolved) path before resolving,
    then verifies the resolved path stays within the workspace.

    Raises:
        FileNotFoundError: If file does not exist.
        OSError: If the path cannot be inspected (see is_reparse_point).
        ValueError: If extension doesn't match, path escapes workspace,
                    symlink points outside workspace, or file is too large.
    """
    raw = Path(path)
    # Check BEFORE resolving — resolve() follows the link, making the check False.
    # is_reparse_point() rather than is_symlink(): it also catches an NTFS junction
    # (which is_symlink() reports False for), and it fails CLOSED, where
    # Path.is_symlink() turns every OSError into "not a link" on Python 3.14 — one
    # of the two interpreters CI runs.
    if is_reparse_point(raw):
        target = raw.resolve()
        if workspace is not None:
            base = Path(workspace).resolve()
            if not target.is_relative_to(base):
                raise ValueError(
                    f"Symlink '{redact(path)}' points outside workspace "
                    f"'{redact(workspace)}'")
    p = raw.resolve()
    if workspace is not None:
        base = Path(workspace).resolve()
        if not p.is_relative_to(base):
            raise ValueError(
                f"Path '{redact(path)}' is outside workspace "
                f"'{redact(workspace)}'")
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {redact(path)}")
    if expected_suffixes and p.suffix.lower() not in expected_suffixes:
        raise ValueError(
            f"Unexpected file type '{p.suffix}' for {redact(path)}. "
            f"Expected: {expected_suffixes}")
    file_size = p.stat().st_size
    if file_size > MAX_FILE_SIZE:
        raise ValueError(
            f"File too large: {redact(path)} is {file_size / 1024 / 1024:.1f} MB "
            f"(limit: {MAX_FILE_SIZE / 1024 / 1024:.0f} MB)"
        )
    return p


def extract_pdf_text(path: str, pages: list[int] | None = None, workspace: str | None = None) -> dict[int, str]:
    """Extract text from PDF, page by page.

    Args:
        path: Path to PDF file.
        pages: Optional list of 0-indexed page numbers. None = all pages.
        workspace: If provided, path must resolve within this directory.

    Returns:
        Dict mapping page number (0-indexed) to extracted text.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If path escapes workspace or file is too large.
        IndexError: If a requested page number is out of range.
    """
    import pdfplumber

    _validate_file_path(path, {".pdf"}, workspace=workspace)

    result = {}
    with pdfplumber.open(path) as pdf:
        num_pages = len(pdf.pages)
        target_pages = pages if pages is not None else range(num_pages)
        for i in target_pages:
            if i < 0 or i >= num_pages:
                raise IndexError(
                    f"Page {i} out of range for {redact(path)} "
                    f"({num_pages} pages)")
            text = pdf.pages[i].extract_text() or ""
            result[i] = text
    return result


def extract_tables(path: str, pages: list[int] | None = None, workspace: str | None = None) -> dict[int, list[list]]:
    """Extract tables from PDF, page by page.

    Args:
        path: Path to PDF file.
        pages: Optional list of 0-indexed page numbers. None = all pages.
        workspace: If provided, path must resolve within this directory.

    Returns:
        Dict mapping page number to list of tables (each table is list of rows).

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If path escapes workspace or file is too large.
        IndexError: If a requested page number is out of range.
    """
    import pdfplumber

    _validate_file_path(path, {".pdf"}, workspace=workspace)

    result = {}
    with pdfplumber.open(path) as pdf:
        num_pages = len(pdf.pages)
        target_pages = pages if pages is not None else range(num_pages)
        for i in target_pages:
            if i < 0 or i >= num_pages:
                raise IndexError(
                    f"Page {i} out of range for {redact(path)} "
                    f"({num_pages} pages)")
            tables = pdf.pages[i].extract_tables() or []
            if tables:
                result[i] = tables
    return result


def extract_excel(path: str, sheet_names: list[str] | None = None, workspace: str | None = None) -> dict[str, list[list]]:
    """Extract data from Excel workbook.

    Args:
        path: Path to .xlsx file.
        sheet_names: Optional list of sheet names. None = all sheets.
        workspace: If provided, path must resolve within this directory.

    Returns:
        Dict mapping sheet name to list of rows (each row is list of cell values).

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If path escapes workspace or file is too large.
    """
    import openpyxl

    _validate_file_path(path, {".xlsx"}, workspace=workspace)

    wb = openpyxl.load_workbook(path, data_only=True)
    result = {}
    targets = sheet_names if sheet_names is not None else wb.sheetnames
    for name in targets:
        if name in wb.sheetnames:
            ws = wb[name]
            result[name] = [list(row) for row in ws.iter_rows(values_only=True)]
    wb.close()
    return result


def extract_csv(path: str, encoding: str = "utf-8-sig", workspace: str | None = None) -> list[list[str]]:
    """Extract data from CSV file.

    Args:
        path: Path to CSV file.
        encoding: File encoding (default utf-8-sig handles BOM).
        workspace: If provided, path must resolve within this directory.

    Returns:
        List of rows (each row is list of string values).

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If path escapes workspace or file is too large.
    """
    _validate_file_path(path, {".csv"}, workspace=workspace)

    with open(path, newline="", encoding=encoding) as f:
        return list(csv.reader(f))


def pdf_page_count(path: str, workspace: str | None = None) -> int:
    """Return number of pages in a PDF.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If path escapes workspace or file is too large.
    """
    import pdfplumber

    _validate_file_path(path, {".pdf"}, workspace=workspace)

    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


def classify_document(text: str) -> str:
    """Classify a tax document based on its text content.

    Args:
        text: Extracted text from document (typically first page).
              If None or empty, returns "unknown".

    Returns one of: w2, 1099-int, 1099-div, 1099-b, 1099-r, 1099-sa,
    1099-nec, 1099-misc, 1099-k, 3921, 3922, brokerage_statement, foreign_bank,
    tax_return, unknown.
    """
    if not text:
        return "unknown"

    text_upper = text.upper()

    patterns = {
        # Forms 3921/3922 FIRST: their keywords are maximally specific, while
        # broker-issued 3921/3922 packets routinely mention generic phrases like
        # "cost basis" (1099-b) or "Form 1040" (tax_return) in their cover
        # text — insertion order is the tiebreak.
        "3921": ["EXERCISE OF AN INCENTIVE STOCK OPTION", "FORM 3921"],
        # Form 3922 = ESPP purchase-info (§423). Same shadowing risk as 3921:
        # broker packets say "COST BASIS", so 3922 must precede 1099-b.
        "3922": ["EMPLOYEE STOCK PURCHASE PLAN UNDER SECTION 423",
                 "TRANSFER OF STOCK ACQUIRED THROUGH AN EMPLOYEE STOCK PURCHASE",
                 "FORM 3922"],
        "w2": ["WAGE AND TAX STATEMENT", "FORM W-2"],
        "1099-int": ["INTEREST INCOME", "FORM 1099-INT"],
        "1099-div": ["DIVIDENDS AND DISTRIBUTIONS", "FORM 1099-DIV"],
        "1099-b": ["PROCEEDS FROM BROKER", "FORM 1099-B", "COST BASIS"],
        "1099-r": ["DISTRIBUTIONS FROM PENSIONS", "FORM 1099-R"],
        "1099-sa": ["DISTRIBUTIONS FROM AN HSA", "FORM 1099-SA"],
        "1099-nec": ["NONEMPLOYEE COMPENSATION", "FORM 1099-NEC"],
        "1099-misc": ["MISCELLANEOUS INFORMATION", "FORM 1099-MISC"],
        "1099-k": ["PAYMENT CARD", "FORM 1099-K"],
        "tax_return": ["FORM 1040", "U.S. INDIVIDUAL INCOME TAX RETURN"],
    }

    for doc_type, keywords in patterns.items():
        if any(kw in text_upper for kw in keywords):
            return doc_type

    # Check for brokerage statement patterns
    brokerage_kw = ["BROKERAGE", "CONSOLIDATED", "TAX REPORTING", "YEAR-END"]
    if any(kw in text_upper for kw in brokerage_kw):
        return "brokerage_statement"

    # Check for non-English (likely foreign bank document)
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    if ascii_ratio < 0.7:
        return "foreign_bank"

    return "unknown"


def scan_workspace(workspace_dir: str) -> list[dict]:
    """Scan workspace for parseable documents (non-recursive).

    Scans the top-level directory only. For nested documents, point to the
    specific subdirectory.

    Returns list of dicts with: path, filename, extension, size_kb, doc_type (if PDF).

    Raises:
        NotADirectoryError: If workspace_dir is not a valid directory.
    """
    workspace = Path(workspace_dir)
    if not workspace.is_dir():
        raise NotADirectoryError(f"Not a directory: {redact(workspace_dir)}")

    extensions = {".pdf", ".xlsx", ".csv"}
    # Skip known output files
    skip_patterns = {"Tax_Return_", "Summary"}

    documents = []
    for f in sorted(workspace.iterdir()):
        # is_reparse_point(), not is_symlink(): a junction is invisible to the
        # latter, and an lstat failure there reads as "not a link" rather than
        # propagating. It fails closed, so an uninspectable entry raises instead
        # of being silently dropped from the document list — a dropped document
        # understates income, which overstates the refund the user acts on.
        if is_reparse_point(f):
            print(f"WARNING: Skipping symlink {redact(f.name)} — copy the "
                  f"file into the workspace instead.",
                  file=sys.stderr)
            continue
        if not f.is_file() or f.suffix.lower() not in extensions:
            continue
        if any(p in f.name for p in skip_patterns):
            continue

        doc = {
            "path": str(f),
            "filename": f.name,
            "extension": f.suffix.lower(),
            "size_kb": round(f.stat().st_size / 1024, 1),
        }

        # Classify by extension
        if f.suffix.lower() == ".pdf":
            try:
                import pdfplumber
                with pdfplumber.open(str(f)) as pdf:
                    first_page_text = pdf.pages[0].extract_text() or "" if pdf.pages else ""
                    doc["doc_type"] = classify_document(first_page_text)
                    doc["page_count"] = len(pdf.pages)
            except ImportError:
                doc["doc_type"] = "error"
                doc["error"] = "pdfplumber not installed"
            except Exception as e:
                doc["doc_type"] = "error"
                # Sanitize error message — avoid leaking document content
                # that may contain SSNs or account numbers. Truncation was the
                # only sanitizing here, and a 200-character prefix of a parser
                # error is where an SSN would sit: redacted at ASSIGNMENT so
                # both consumers are covered, the stderr warning below and the
                # `error` field that rides out in the document list.
                err_msg = redact(e)
                if len(err_msg) > 200:
                    err_msg = err_msg[:200] + "..."
                doc["error"] = f"{type(e).__name__}: {err_msg}"
                print(f"WARNING: Failed to classify {redact(f.name)}: "
                      f"{type(e).__name__}: {redact(err_msg)}",
                      file=sys.stderr)
        elif f.suffix.lower() == ".xlsx":
            doc["doc_type"] = "excel"
        elif f.suffix.lower() == ".csv":
            doc["doc_type"] = "csv"

        documents.append(doc)

    return documents


if __name__ == "__main__":
    # CLI: python parse_utils.py scan <dir>
    # CLI: python parse_utils.py classify <pdf>
    # CLI: python parse_utils.py extract <pdf> [page_num]
    if len(sys.argv) < 3:
        print("Usage: python parse_utils.py <scan|classify|extract|tables> <path> [page]")
        sys.exit(1)

    cmd, path = sys.argv[1], sys.argv[2]

    try:
        if cmd == "scan":
            docs = scan_workspace(path)
            print(json.dumps(docs, indent=2))
        elif cmd == "classify":
            text = extract_pdf_text(path, pages=[0])
            doc_type = classify_document(text.get(0, ""))
            print(f"Document type: {doc_type}")
        elif cmd == "extract":
            pages = [int(sys.argv[3])] if len(sys.argv) > 3 else None
            text = extract_pdf_text(path, pages=pages)
            for pg, content in sorted(text.items()):
                print(f"=== Page {pg} ===")
                print(content)
                print()
        elif cmd == "tables":
            pages = [int(sys.argv[3])] if len(sys.argv) > 3 else None
            tables = extract_tables(path, pages=pages)
            print(json.dumps(tables, indent=2, default=str))
        else:
            print(f"Error: Unknown command '{redact(cmd)}'. "
              f"Valid commands: scan, classify, extract, tables",
                  file=sys.stderr)
            sys.exit(1)
    except (FileNotFoundError, ValueError, NotADirectoryError) as e:
        print(f"Error: {redact(e)}", file=sys.stderr)
        sys.exit(1)
