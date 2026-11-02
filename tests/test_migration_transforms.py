from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from modelbridge.application.migration_engine import (
    RecordError,
    canonical,
    ordered_tables,
    transform,
)
from modelbridge.migration_contracts import FieldMapping, PlanInput

NAMESPACE = "ac9135af-f0b8-4da0-bca3-cbb34efbb518"


@pytest.mark.parametrize(
    "operation,value,expected",
    [
        ("date_dmy", "31/10/2026", date(2026, 10, 31)),
        ("date_iso", "2026-11-01", date(2026, 11, 1)),
        ("decimal", "100.50", Decimal("100.50")),
        ("integer", "0042", 42),
        ("trim", " Student One ", "Student One"),
        ("lower", "STUDENT@EXAMPLE.ORG", "student@example.org"),
        ("string", 42, "42"),
        ("identity", None, None),
    ],
)
def test_registered_transformations(operation: str, value: object, expected: object) -> None:
    rule = FieldMapping.model_validate({"transform": operation})
    assert transform(rule, value, NAMESPACE, "students") == expected


@pytest.mark.parametrize(
    "operation,value",
    [
        ("date_dmy", "not-a-date"),
        ("date_iso", "31/10/2026"),
        ("integer", "1.2"),
        ("integer", True),
        ("decimal", "NaN"),
        ("decimal", "bad-amount"),
        ("decimal", "1e999999999"),
        ("decimal", "1e-999999999"),
    ],
)
def test_transformation_failures_are_explicit(operation: str, value: object) -> None:
    rule = FieldMapping.model_validate({"transform": operation})
    with pytest.raises(RecordError, match="transformation_failed"):
        transform(rule, value, NAMESPACE, "students")


def test_identifier_crosswalk_values_are_stable_and_do_not_collapse_identifier_formats() -> None:
    rule = FieldMapping(transform="uuid")
    first = transform(rule, "001", NAMESPACE, "students")
    assert isinstance(first, UUID)
    assert first == transform(rule, "001", NAMESPACE, "students")
    assert first != transform(rule, 1, NAMESPACE, "students")
    assert first != transform(rule, "001", NAMESPACE, "teachers")


def test_canonical_decimal_comparison_preserves_high_precision_without_context_rounding() -> None:
    first = Decimal("123456789012345678901234567890.123456789")
    different = Decimal("123456789012345678901234567890.123456788")
    assert canonical(first) != canonical(different)
    assert canonical(Decimal("100.5000")) == canonical(Decimal("100.50"))


def test_mapping_rejects_unregistered_transformations_and_dependency_cycles() -> None:
    with pytest.raises(ValidationError):
        FieldMapping.model_validate({"transform": "execute_python", "value": "not executable"})
    spec = PlanInput.model_validate(
        {
            "source_snapshot_id": "synthetic-source",
            "destination_snapshot_id": "synthetic-destination",
            "tables": [
                {
                    "source_table": "students",
                    "destination_table": "students",
                    "source_key": "id",
                    "destination_key": "id",
                    "fields": {
                        "id": {"source": "id"},
                        "parent": {
                            "source": "parent",
                            "transform": "reference",
                            "reference_table": "students",
                        },
                    },
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="acyclic"):
        ordered_tables(spec)
