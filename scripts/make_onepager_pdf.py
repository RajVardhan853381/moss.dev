"""Render ONE_PAGER.md to ONE_PAGER.pdf and report the page count (the brief says: 1 page max)."""
import re, sys, os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from pypdf import PdfReader

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
size = float(sys.argv[1]) if len(sys.argv) > 1 else 9.2
SRC = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "ONE_PAGER.md")
src = open(SRC).read()


def inline(t):
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"`([^`]+)`", r'<font face="Courier" size="%.1f">\1</font>' % (size - 0.6), t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![*\w])\*([^*]+)\*(?![*\w])", r"<i>\1</i>", t)
    return t


body = ParagraphStyle("b", fontName="Helvetica", fontSize=size, leading=size * 1.27, spaceAfter=size * 0.42, textColor=colors.HexColor("#191919"))
h1 = ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=size + 5, leading=size + 7, spaceAfter=4)
h2 = ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=size + 1.6, leading=size + 3.5, spaceBefore=size * 0.5, spaceAfter=2, textColor=colors.HexColor("#1b64be"))
flow = []
for block in re.split(r"\n\s*\n", src.strip()):
    block = block.strip()
    if block.startswith("# "):
        flow.append(Paragraph(inline(block[2:]), h1))
    elif block.startswith("## "):
        head, _, rest = block.partition("\n")
        flow.append(Paragraph(inline(head[3:]), h2))
        if rest.strip():
            flow.append(Paragraph(inline(" ".join(rest.split("\n"))), body))
    else:
        flow.append(Paragraph(inline(" ".join(block.split("\n"))), body))
out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(ROOT, "ONE_PAGER.pdf")
doc = SimpleDocTemplate(out, pagesize=letter, leftMargin=0.62 * inch, rightMargin=0.62 * inch, topMargin=0.5 * inch, bottomMargin=0.45 * inch,
                        title="Moss Inference: work sample", author="Kumar Rajvardhan")
doc.build(flow)
print("font", size, "pt -> pages:", len(PdfReader(out).pages))
