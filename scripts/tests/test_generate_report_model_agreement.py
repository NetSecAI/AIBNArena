"""The report says whether anything confirmed the model it names.

Three names are recorded per episode: what the judge was told, what the subject says
it asked for, and what the provider's own response reported. Only the third is
evidence of what served the episode. A report that showed the declared name alone
could not be read for a gateway that aliased it.
"""
from __future__ import annotations

import pytest

from scripts.generate_report import model_agreement, model_agreement_html


def verdict(declared, subject, provider):
    return model_agreement({"configured_model": declared, "sut_reported_model": subject,
                            "provider_reported_model": provider})


def test_three_names_that_agree_are_confirmed() -> None:
    assert verdict("m", "m", "m")["agree"] is True
    assert "confirmed by the subject and the provider" in model_agreement_html(
        {"model_agreement": verdict("m", "m", "m")})


def test_a_provider_that_served_another_model_is_a_disagreement() -> None:
    """The gateway aliased the name instead of refusing it: the case the other two
    names cannot show, because both of them are the request."""
    assert verdict("gpt-4o-mini", "gpt-4o-mini", "fr-gpt-5.4")["agree"] is False
    row = model_agreement_html({"model_agreement": verdict("gpt-4o-mini", "gpt-4o-mini", "fr-gpt-5.4")})
    assert "DISAGREEMENT" in row
    assert "provider fr-gpt-5.4" in row


@pytest.mark.parametrize("declared,subject,provider", [
    (None, "m", "m"),   # the judge was not told, so it contradicts nothing
    ("m", "m", None),   # the provider reported no model, so it said nothing
])
def test_an_absent_name_is_not_a_disagreement(declared, subject, provider) -> None:
    assert verdict(declared, subject, provider)["agree"] is True


def test_one_name_alone_is_unchecked_rather_than_agreed() -> None:
    result = verdict(None, "m", None)
    assert result["agree"] is None
    assert result["missing"] == ["declared", "provider"]
    assert "unconfirmed" in model_agreement_html({"model_agreement": result})


def test_the_row_falls_back_to_computing_the_verdict() -> None:
    """An older report has the three names and no verdict; the row still states one."""
    row = model_agreement_html({"configured_model": "m", "sut_reported_model": "m",
                                "provider_reported_model": "other"})
    assert "DISAGREEMENT" in row
