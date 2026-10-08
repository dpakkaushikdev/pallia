"""Locate Pallia's signature space from the PDF's own footer coordinates."""


def find_footer_box(page):
    width = float(page.mediabox.width)
    company_lines, signatory_lines = [], []

    def visit(text, cm, tm, font, size):
        cm = cm or (1, 0, 0, 1, 0, 0)
        tm = tm or (1, 0, 0, 1, 0, 0)
        x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
        y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
        normalized = " ".join(text.casefold().split())
        if normalized.startswith("for pallia"):
            company_lines.append((x, y))
        if "authorised signatory" in normalized or "authorized signatory" in normalized:
            signatory_lines.append((x, y))

    page.extract_text(visitor_text=visit)
    pairs = [(company, signatory) for company in company_lines for signatory in signatory_lines
             if signatory[1] < company[1] and abs(company[0] - signatory[0]) < width * 0.3]
    if not pairs:
        raise ValueError("Could not locate both 'For Pallia Logistics Private Limited' (or 'For Pallia Trans Logistics') and 'Authorised Signatory' in the PDF footer.")
    company, signatory = min(pairs, key=lambda pair: pair[0][1] - pair[1][1])
    if company[1] - signatory[1] < 20:
        raise ValueError("Both footer lines were found, but there is less than 20 points of space between them for the signature.")

    # Mahindra has both labels at the left edge. Tata / Accounts templates
    # put them on the right. Preserve that side instead of fixing all PDFs
    # to the right-hand corner. The vertical bounds always follow the labels.
    if company[0] < width / 2 and signatory[0] < width / 2:
        x1 = max(float(page.mediabox.left) + 8, min(company[0], signatory[0]) + 8)
        x2 = min(x1 + width * 0.33, float(page.mediabox.right) - 15)
        margin = 8 if company[1] - signatory[1] >= 30 else 4
    else:
        x1, x2, margin = width * 0.57, float(page.mediabox.right) - 15, 4
    return (x1, signatory[1] + margin, x2, company[1] - margin)
