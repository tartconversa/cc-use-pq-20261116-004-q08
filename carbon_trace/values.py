import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import InvalidInput, require


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def identifier(value, label: str = "identifier") -> str:
    require(isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)),
            f"{label} must be 1-64 simple characters", field=label)
    return value


def day(value, label: str = "day") -> str:
    require(isinstance(value, str), f"{label} must be an ISO date", field=label)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInput(f"{label} must be an ISO date") from exc
    require(parsed.isoformat() == value, f"{label} must be YYYY-MM-DD", field=label)
    return value


def instant(value, label: str = "at") -> str:
    require(isinstance(value, str), f"{label} must be a timestamp", field=label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidInput(f"{label} must be an ISO timestamp") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            f"{label} needs a timezone", field=label)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def local_day(value: str, zone: str) -> str:
    return datetime.fromisoformat(instant(value).replace("Z", "+00:00")).astimezone(ZoneInfo(zone)).date().isoformat()


def zone_name(value: str) -> str:
    require(isinstance(value, str) and bool(value), "timezone is required", field="timezone")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise InvalidInput("unknown timezone", detail={"timezone": value}) from exc
    return value


def decimal(value, label: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    require(not isinstance(value, bool), f"{label} must be numeric", field=label)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidInput(f"{label} must be numeric") from exc
    require(result.is_finite(), f"{label} must be finite", field=label)
    if positive:
        require(result > 0, f"{label} must be positive", field=label)
    if nonnegative:
        require(result >= 0, f"{label} cannot be negative", field=label)
    require(abs(result.as_tuple().exponent) <= 6 and len(result.as_tuple().digits) <= 15,
            f"{label} exceeds supported precision", field=label)
    return result


def as_number(value: Decimal) -> str:
    return format(value, "f")


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def mapping(value, label: str = "payload") -> dict:
    require(isinstance(value, dict), f"{label} must be an object", field=label)
    return value


def sequence(value, label: str = "events") -> list:
    require(isinstance(value, list) and 0 < len(value) <= 500,
            f"{label} must contain 1-500 items", field=label)
    return value


def text(value, label: str, *, max_length: int = 160) -> str:
    require(isinstance(value, str) and 0 < len(value.strip()) <= max_length,
            f"{label} must contain 1-{max_length} characters", field=label)
    return value.strip()

