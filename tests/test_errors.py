import pytest

from arca_integration.core.errors import ArcaError, ConstanciaError


def test_subclasses_carry_message_and_detail():
    err = ConstanciaError("constancia bloqueada", detail="biometric data missing")

    assert isinstance(err, ArcaError)
    assert err.message == "constancia bloqueada"
    assert err.detail == "biometric data missing"


def test_detail_is_optional():
    err = ArcaError("boom")

    assert err.detail is None
    assert str(err) == "boom"
