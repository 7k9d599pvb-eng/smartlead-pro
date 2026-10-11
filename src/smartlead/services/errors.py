from smartlead.i18n import tr


class BusinessError(Exception):
    """A safe, user-facing business error."""

    def __init__(self, key: str, **variables: object) -> None:
        self.key = key
        self.variables = variables
        super().__init__(key)

    def __str__(self) -> str:
        return tr(self.key, **self.variables)


class ValidationError(BusinessError):
    pass


class AccessDenied(BusinessError):
    pass


class NotFound(BusinessError):
    pass


class SlotUnavailable(BusinessError):
    pass


class InvalidTransition(BusinessError):
    pass
