from datetime import UTC, datetime

from prov.models import ProvRecord, Subject
from prov.store.records import decode_lines, encode, new_record


def test_record_round_trip_and_stable_id():
    at = datetime(2026, 10, 20, tzinfo=UTC)
    r1 = new_record(Subject(commit="a" * 40, tree="b" * 40), "test", "uat", "PASSED", at=at, suite="pytest")
    r2 = new_record(Subject(commit="a" * 40, tree="b" * 40), "test", "uat", "PASSED", at=at, suite="pytest")
    assert r1.id == r2.id and len(r1.id) == 40
    line = encode(r1)
    assert " " not in line and "\n" not in line
    assert ProvRecord.model_validate_json(line) == r1
    assert decode_lines(line + "\nnot json\n") == [r1]
