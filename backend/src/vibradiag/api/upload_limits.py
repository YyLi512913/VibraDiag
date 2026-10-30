"""Bound multipart bytes even without Content-Length, before disk spooling grows."""
from starlette.formparsers import MultiPartException
from vibradiag.analysis.csv_input import MAX_FILE_BYTES

BODY_LIMIT = MAX_FILE_BYTES + 64*1024
LIMIT_MESSAGE = 'measurement multipart body exceeds limit'


class MeasurementBodyLimit:
    def __init__(self, app):
        self.app=app

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope['method']!='POST' or scope['path']!='/api/v1/measurements':
            return await self.app(scope,receive,send)
        received=0

        async def limited_receive():
            nonlocal received
            message=await receive()
            received+=len(message.get('body',b''))
            if received>BODY_LIMIT:
                # Starlette closes temporary multipart files on MultiPartException.
                raise MultiPartException(LIMIT_MESSAGE)
            return message

        await self.app(scope,limited_receive,send)
