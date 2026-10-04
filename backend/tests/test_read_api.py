from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from pawspot.api.read import _decode_cursor, _encode_cursor


def test_cursor_round_trip_and_validation() -> None:
    moment = datetime(2026, 10, 4, 12, 30, tzinfo=UTC)
    identifier = uuid4()
    assert _decode_cursor(_encode_cursor(moment, identifier)) == (moment, identifier)
    for invalid in ("!", "a", "a" * 257, "WzEsMl0"):
        with pytest.raises(HTTPException) as error:
            _decode_cursor(invalid)
        assert error.value.status_code == 422
