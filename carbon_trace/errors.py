class DomainError(Exception):
    status = 422
    code = "domain_error"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"error": self.code, "message": str(self), "detail": self.detail}


class InvalidInput(DomainError):
    code = "invalid_input"


class Missing(DomainError):
    status = 404
    code = "not_found"


class Conflict(DomainError):
    status = 409
    code = "conflict"


class Stale(Conflict):
    code = "stale_revision"


class Invariant(Conflict):
    code = "business_invariant"


def require(condition: bool, message: str, *, field: str | None = None) -> None:
    if not condition:
        raise InvalidInput(message, detail={"field": field} if field else None)

