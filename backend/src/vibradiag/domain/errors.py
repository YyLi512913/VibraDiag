class ApplicationError(Exception):
    """Safe user-facing error; HTTP mapping belongs to the API boundary."""

class InvalidInput(ApplicationError,ValueError):
    pass

class ResourceNotFound(ApplicationError):
    pass

class ResourceBusy(ApplicationError):
    pass

class PayloadTooLarge(ApplicationError):
    pass
