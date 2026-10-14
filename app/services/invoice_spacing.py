"""Measure the blank area before Total while retaining existing footer layouts."""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm


def space_before_total(prefix, items, width, top_margin=10 * mm):
    # SimpleDocTemplate has a 10 mm top margin and 6 pt frame inset.
    used = top_margin + 6
    for flowable in prefix:
        used += flowable.getSpaceBefore()
        used += flowable.wrap(width, A4[1])[1]
        used += flowable.getSpaceAfter()
    items.wrap(width, A4[1])
    used += items._height - items._rowHeights[-1]
    # Total starts 225 mm below the page top; the existing footer follows it.
    return max(0, 225 * mm - used)
