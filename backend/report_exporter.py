"""
report_exporter.py

Exports a completed research report as a PDF to the configured reports
directory.  Uses fpdf2 for lightweight, pure-Python PDF generation with
no system-library dependencies.

The export runs in a thread pool (asyncio.to_thread) so it never blocks
the event loop.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default reports directory — resolved relative to this file so it lands at
# <repo_root>/data/reports when running locally outside Docker.
_DEFAULT_REPORTS_DIR = Path(__file__).parent.parent / "data" / "reports"

# Section headers produced by the ReportComposer / assembler in orchestrator.py.
# Used to detect heading lines when rendering the PDF.
_SECTION_HEADERS = frozenset(
    {
        "RESEARCH REPORT",
        "EXECUTIVE SUMMARY",
        "KEY FINDINGS",
        "NOVEL INSIGHTS",
        "RECOMMENDATIONS",
        "KNOWLEDGE GAPS",
        "REFERENCES",
        "CROSS-REFERENCE CAVEATS",
        "UNRESOLVED SOURCE CONTRADICTIONS",
    }
)


def _get_reports_dir() -> Path:
    raw = os.getenv("REPORTS_DIR", str(_DEFAULT_REPORTS_DIR))
    return Path(raw)


# Common English stopwords that add no meaning to a filename.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "up", "about", "into", "through", "is",
        "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "will", "would", "could", "should", "may",
        "might", "shall", "can", "what", "where", "when", "why", "how",
        "which", "who", "whom", "this", "that", "these", "those", "it",
        "its", "we", "they", "you", "i", "my", "our", "your", "their",
        "his", "her", "some", "any", "all", "each", "as", "if", "than",
        "so", "yet", "both", "just", "more", "most", "also", "me", "us",
    }
)


def _query_to_slug(query: str, max_words: int = 5) -> str:
    """
    Produce a short, human-readable filename slug from a research query.

    Strips punctuation and common stopwords, takes up to *max_words* of the
    remaining meaningful terms, title-cases them, and joins with underscores.

    Example: "What are the geopolitical implications of rare earth scarcity?"
             → "Geopolitical_Implications_Rare_Earth_Scarcity"
    """
    # Remove non-alphanumeric characters (keep spaces and hyphens)
    cleaned = re.sub(r"[^\w\s-]", " ", query).strip()
    words = cleaned.split()
    meaningful = [
        w for w in words if w.lower() not in _STOPWORDS and len(w) > 1
    ]
    chosen = meaningful[:max_words] if meaningful else words[:max_words]
    slug = "_".join(w.capitalize() for w in chosen)
    return slug if slug else "report"


def _extract_title(document: str, query: str) -> str:
    """
    Pull the report title from the first RESEARCH REPORT header line in the
    document, e.g. "RESEARCH REPORT: Advances in Quantum Computing 2026".
    Falls back to the original query if no explicit title is found.
    """
    for line in document.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("RESEARCH REPORT:"):
            parts = stripped.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()
    return query


def _safe_text(text: str) -> str:
    """
    Encode text so that fpdf2's built-in core fonts (Windows-1252 superset)
    can render it without raising UnicodeEncodeError.  Non-representable
    characters are replaced with '?'.
    """
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _generate_pdf(document: str, query: str, output_path: Path) -> None:
    """
    Render *document* (the plain-text research report produced by the
    ReportComposer / orchestrator assembler) to a PDF at *output_path*.

    Layout
    ------
    - Cover block  : document type + derived title + generation timestamp
    - Body         : section headings (bold, shaded) followed by body paragraphs
    - Footer       : page numbers

    Called via asyncio.to_thread so it never blocks the event loop.
    """
    from fpdf import FPDF  # lazy import so startup isn't slowed if fpdf2 is absent

    class _ReportPDF(FPDF):
        def header(self) -> None:
            if self.page_no() > 1:
                self.set_font("Helvetica", "I", 8)
                self.set_text_color(128, 128, 128)
                self.cell(0, 8, "Research Assistant Report", align="C")
                self.ln(4)
                self.set_text_color(0, 0, 0)

        def footer(self) -> None:
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"Page {self.page_no()}", align="C")
            self.set_text_color(0, 0, 0)

    pdf = _ReportPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(left=20, top=20, right=20)
    pdf.add_page()

    # ── Cover block ──────────────────────────────────────────────────────────
    pdf.set_fill_color(30, 58, 138)  # brand indigo
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(
        0, 14, "RESEARCH REPORT", fill=True, align="C", new_x="LMARGIN", new_y="NEXT"
    )

    title = _extract_title(document, query)
    pdf.set_fill_color(255, 255, 255)
    pdf.set_text_color(20, 20, 80)
    pdf.set_font("Helvetica", "B", 13)
    pdf.ln(4)
    pdf.multi_cell(0, 8, _safe_text(title), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(0, 6, f"Generated: {generated}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    # ── Body — parse lines into headings and paragraphs ─────────────────────
    # The document starts with "RESEARCH REPORT\nQuery: <query>\n\n..."
    # We skip the first two lines (already rendered above) and begin the body
    # from the first section header onward.
    lines = document.splitlines()
    in_body = False
    refs_mode = False  # tighter line-height in the REFERENCES section

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        # Skip until we hit the first named section header
        if not in_body:
            # The RESEARCH REPORT / Query: header block is already on the cover;
            # start the body on the first real section.
            is_section = any(upper.startswith(h) for h in _SECTION_HEADERS)
            if is_section and not upper.startswith("RESEARCH REPORT"):
                in_body = True
            else:
                continue

        is_section = any(upper.startswith(h) for h in _SECTION_HEADERS)

        if is_section:
            refs_mode = upper.startswith("REFERENCES")
            pdf.ln(3)
            pdf.set_fill_color(232, 238, 255)
            pdf.set_text_color(20, 20, 80)
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(
                0,
                9,
                _safe_text(stripped.upper()),
                fill=True,
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 10)
            pdf.ln(1)
        elif stripped:
            if refs_mode:
                # Reference entries: slightly smaller, monospaced-ish
                pdf.set_font("Helvetica", "", 9)
                pdf.multi_cell(
                    0, 5, _safe_text(stripped), align="L", new_x="LMARGIN", new_y="NEXT"
                )
                pdf.set_font("Helvetica", "", 10)
            else:
                pdf.multi_cell(
                    0, 6, _safe_text(stripped), align="L", new_x="LMARGIN", new_y="NEXT"
                )
        else:
            pdf.ln(3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))


async def export_report_pdf(
    document: str,
    query: str,
    title: str = "",
    session_id: Optional[str] = None,
) -> Optional[Path]:
    """
    Asynchronously render *document* to a PDF in the configured reports
    directory.

    File naming: ``{YYYY-MM-DD}_{session_id[:8]}_{title_slug}.pdf``

    Returns the output Path on success, or None if the export fails
    (e.g. fpdf2 not installed, disk error).  Failures are logged as
    warnings so they never interrupt the main pipeline.
    """
    try:
        reports_dir = _get_reports_dir()
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug_base = title or query
        slug = _query_to_slug(slug_base)
        sid_part = (session_id or "cli")[:8]
        filename = f"{date_str}_{sid_part}_{slug}.pdf"
        output_path = reports_dir / filename

        await asyncio.to_thread(_generate_pdf, document, query, output_path)
        logger.info("[ReportExporter] PDF saved: %s", output_path)
        return output_path
    except ImportError:
        logger.warning(
            "[ReportExporter] fpdf2 not installed — skipping PDF export. "
            "Install with: pip install fpdf2"
        )
        return None
    except Exception as exc:
        logger.warning("[ReportExporter] PDF export failed: %s", exc)
        return None
