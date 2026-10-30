class MeasurementError(Exception):
    def __init__(self, code, message, *, status=422, field=None, row=None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.field, self.row = field, row
