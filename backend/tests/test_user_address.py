"""User form-of-address: how the guide addresses the LISTENER grammatically ("ты прошёл/прошла").
Optional, self-set, neutral by default (actively sidesteps gendered 2nd-person forms)."""

from __future__ import annotations

from app.services.agent.narrator import _address_instr
from app.shared.schemas import WSSetAddressForm


def test_address_instruction_by_form() -> None:
    assert "МУЖСКОМ" in _address_instr("masculine")
    assert "ЖЕНСКОМ" in _address_instr("feminine")
    # empty (unset) and any unknown value fall back to the neutral instruction
    assert "НЕЙТРАЛЬНО" in _address_instr("")
    assert "НЕЙТРАЛЬНО" in _address_instr("nonbinary-or-typo")


def test_neutral_tells_model_to_avoid_gendered_forms() -> None:
    # The default must ACTIVELY instruct neutrality (RU narration otherwise defaults to masculine).
    instr = _address_instr("")
    assert "ИЗБЕГАЙ" in instr and "НЕ угадывай" in instr


def test_ws_address_form_message() -> None:
    m = WSSetAddressForm.model_validate({"type": "address_form", "form": "feminine"})
    assert m.form == "feminine"
    # form is optional — defaults to "" (neutral)
    assert WSSetAddressForm.model_validate({"type": "address_form"}).form == ""
