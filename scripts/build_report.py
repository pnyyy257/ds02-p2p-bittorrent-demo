"""Build a Vietnamese PDF report from docs/REPORT.md (requires reportlab).

Optional: pip install reportlab
"""
from pathlib import Path
import re
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether

ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / 'docs' / 'REPORT.md'
OUT = ROOT / 'docs' / 'report.pdf'

font_candidates = [
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    Path('/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf'),
    Path('C:/Windows/Fonts/arial.ttf'),
]
font = next((path for path in font_candidates if path.exists()), None)
if font is None:
    raise SystemExit('Requires a Unicode TrueType font (DejaVu Sans, Noto Sans or Arial).')
pdfmetrics.registerFont(TTFont('Vietnamese', str(font)))

style_title = ParagraphStyle('TitleVN', fontName='Vietnamese', fontSize=20, leading=30,
                             textColor=colors.HexColor('#173155'), spaceAfter=24, alignment=TA_CENTER)
style_sub = ParagraphStyle('SubVN', fontName='Vietnamese', fontSize=13, leading=21,
                           textColor=colors.HexColor('#245b80'), spaceAfter=16, alignment=TA_CENTER)
style_chapter = ParagraphStyle('ChapterVN', fontName='Vietnamese', fontSize=15, leading=23,
                               textColor=colors.HexColor('#173155'), spaceAfter=19)
style_body = ParagraphStyle('BodyVN', fontName='Vietnamese', fontSize=10.3, leading=17.6,
                            textColor=colors.HexColor('#27384a'), spaceAfter=12, alignment=TA_LEFT)
style_note = ParagraphStyle('NoteVN', parent=style_body, textColor=colors.HexColor('#606d7a'), fontSize=9)


def inline_markup(s):
    # Simple markdown bold/backtick support, escaped for reportlab.
    s = escape(s.strip())
    s = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'`([^`]+)`', r'<font color="#0b7593">\1</font>', s)
    return s


def footer(canvas, doc):
    canvas.saveState()
    width, _ = A4
    canvas.setStrokeColor(colors.HexColor('#d5e1e8'))
    canvas.line(18 * mm, 18 * mm, width - 18 * mm, 18 * mm)
    canvas.setFont('Vietnamese', 8)
    canvas.setFillColor(colors.HexColor('#687d8e'))
    canvas.drawString(18 * mm, 13 * mm, 'DS02  |  P2P FILE-SHARING SYSTEM  |  TECHNICAL REPORT DRAFT')
    canvas.drawRightString(width - 18 * mm, 13 * mm, f'{doc.page}')
    canvas.restoreState()


def main():
    text = IN.read_text(encoding='utf8')
    sections = re.split(r'^## ', text, flags=re.M)
    cover = sections[0]
    document = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
                                 topMargin=22*mm, bottomMargin=27*mm,
                                 title='DS02 P2P File-Sharing - Technical Report Draft',
                                 author='DS02 Project Team (complete before submission)')
    story = [Spacer(1, 34*mm), Paragraph('DISTRIBUTED SYSTEMS', style_sub),
             Paragraph('DS02 — P2P FILE-SHARING SYSTEM', style_title),
             Paragraph('SIMULATING BITTORRENT', style_sub), Spacer(1, 13*mm),
             Paragraph('BÁO CÁO KỸ THUẬT — BẢN NHÁP', style_title), Spacer(1, 13*mm)]
    for line in cover.splitlines():
        if line.startswith('**'):
            story.append(Paragraph(inline_markup(line.rstrip('  ')), style_body))
        if line.startswith('*Đây'):
            story.append(Spacer(1, 16*mm))
            story.append(Paragraph(inline_markup(line.strip('*')), style_note))
    story.append(PageBreak())
    for section in sections[1:]:
        blocks = [line.strip() for line in section.split('\n\n') if line.strip()]
        if not blocks:
            continue
        heading, _, first_content = blocks[0].partition('\n')
        story.append(Paragraph(inline_markup(heading), style_chapter))
        if first_content.strip():
            story.append(Paragraph(inline_markup(first_content), style_body))
        for block in blocks[1:]:
            textblock = ' '.join(line.strip() for line in block.splitlines())
            story.append(Paragraph(inline_markup(textblock), style_body))
        story.append(PageBreak())
    if isinstance(story[-1], PageBreak):
        story.pop()
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    print('Generated', OUT)


if __name__ == '__main__':
    main()
