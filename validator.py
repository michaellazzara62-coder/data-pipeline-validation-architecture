import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Tuple
from jsonschema import Draft202012Validator


class RoutingDecision(Enum):
    REJECT = "REJECT"          # Fatal contract breach (e.g., missing primary keys)
    QUARANTINE = "QUARANTINE"  # Recoverable formatting/type drift
    ACCEPT = "ACCEPT"          # Clean payload ready for staging -> core


@dataclass
class ValidationResult:
    decision: RoutingDecision
    errors: List[str]
    payload: Dict[str, Any]


class PipelineBoundaryValidator:
    FATAL_FIELDS = {"submission_id", "partner_id"}

    def __init__(self, schema_path: Path):
        with schema_path.open("r", encoding="utf-8") as f:
            schema = json.load(f)
        self.validator = Draft202012Validator(schema)

    def evaluate(self, payload: Dict[str, Any]) -> ValidationResult:
        errors = sorted(self.validator.iter_errors(payload), key=lambda e: e.path)

        if not errors:
            return ValidationResult(
                decision=RoutingDecision.ACCEPT,
                errors=[],
                payload=payload,
            )

        error_messages = [
            f"{'.'.join(str(p) for p in err.path) or 'root'}: {err.message}"
            for err in errors
        ]

        # Classify fatal vs. recoverable errors
        is_fatal = any(
            err.validator == "required"
            and any(field in err.message for field in self.FATAL_FIELDS)
            for err in errors
        )

        decision = RoutingDecision.REJECT if is_fatal else RoutingDecision.QUARANTINE
        return ValidationResult(
            decision=decision,
            errors=error_messages,
            payload=payload,
        )


def route_payload(raw_payload: Dict[str, Any], validator: PipelineBoundaryValidator) -> Tuple[str, List[str]]:
    """Routes incoming partner payloads based on boundary validation rules."""
    result = validator.evaluate(raw_payload)

    if result.decision == RoutingDecision.REJECT:
        # Return immediate 422 feedback to partner API client
        return "422_UNPROCESSABLE_ENTITY", result.errors

    if result.decision == RoutingDecision.QUARANTINE:
        # Write to staging_quarantine table for weekly ops review
        return "202_QUEUED_FOR_REVIEW", result.errors

    # Write to clean staging table for downstream transformation
    return "200_STAGED_FOR_CORE", []


if __name__ == "__main__":
    schema_file = Path(__file__).parent.parent / "schemas" / "partner_submission_v1.json"
    boundary_validator = PipelineBoundaryValidator(schema_file)

    sample_batch = [
        # 1. Valid payload -> ACCEPT
        {
            "submission_id": "SUB-104921",
            "partner_id": "ORG-CT-09",
            "reporting_period": "2026-10-01",
            "metrics": {"program_count": 12, "total_attendance": 450},
        },
        # 2. Recoverable drift (string instead of int) -> QUARANTINE
        {
            "submission_id": "SUB-104922",
            "partner_id": "ORG-CT-14",
            "reporting_period": "2026-10-01",
            "metrics": {"program_count": "12", "total_attendance": 310},
        },
        # 3. Fatal breach (missing submission_id) -> REJECT
        {
            "partner_id": "ORG-CT-22",
            "reporting_period": "2026-10-01",
            "metrics": {"program_count": 5, "total_attendance": 120},
        },
    ]

    for item in sample_batch:
        status, issues = route_payload(item, boundary_validator)
        print(f"Status: {status} | Issues: {issues}")
