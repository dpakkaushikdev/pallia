"""Pallia invoice generation: rows the page cannot hold and amounts that are not numbers are refused."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.billing import _check_invoice_rows
from app.services.invoice_pdf import MAX_ROWS


def _lr(amounts: list[str]) -> dict:
    return {"gcn_no": "112032145", "vehicle_no": "NL01AK0496",
            "do_rows": [{"delivery_order_no": f"74123045{i:02d}", "total_amount": a} for i, a in enumerate(amounts)]}


def test_valid_rows_pass():
    _check_invoice_rows([_lr(["5,000", "3000.50", ""])])


def test_more_rows_than_the_page_holds_are_refused():
    with pytest.raises(HTTPException) as caught:
        _check_invoice_rows([_lr(["100"] * (MAX_ROWS + 1))])
    assert caught.value.status_code == 400
    assert "at most 12" in caught.value.detail


@pytest.mark.parametrize("amount", ["5000/-", "Rs 5000", "1.2.3", "Infinity", "-5"])
def test_amount_that_is_not_a_number_is_refused(amount):
    with pytest.raises(HTTPException) as caught:
        _check_invoice_rows([_lr([amount])])
    assert caught.value.status_code == 400
    assert "is not a number" in caught.value.detail
