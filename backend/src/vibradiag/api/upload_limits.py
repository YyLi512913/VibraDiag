"""Bound multipart bytes even without Content-Length, before disk spooling grows."""
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from vibradiag.analysis.csv_input import MAX_FILE_BYTES

BODY_LIMIT = MAX_FILE_BYTES + 64*1024
LIMIT_MESSAGE = 'measurement multipart body exceeds limit'


class MeasurementBodyLimit:
    def __init__(self, app):
        self.app=app

    async def __call__(self, scope, receive, send):
        if scope['type']=='http' and scope['method']=='POST' and scope.get('path','').startswith('/api/v1/knowledge-indexes'):
            body=bytearray()
            while True:
                message=await receive()
                if message['type']=='http.disconnect':return
                part=message.get('body',b'')
                if len(body)+len(part)>16*1024:
                    response=JSONResponse(status_code=413,content={'error':{'code':'KNOWLEDGE_INDEX_BODY_LIMIT','message':'Knowledge index JSON body exceeds 16 KiB'},
                        'request_id':str(scope.get('state',{}).get('request_id',''))})
                    return await response(scope,receive,send)
                body.extend(part)
                if not message.get('more_body',False):break
            delivered=False
            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered=True
                    return {'type':'http.request','body':bytes(body),'more_body':False}
                return await receive()
            return await self.app(scope,bounded_receive,send)
        measurement=scope.get('path')=='/api/v1/measurements'
        knowledge=scope.get('path')=='/api/v1/knowledge-documents' or (scope.get('path','').startswith('/api/v1/knowledge-documents/') and scope.get('path','').endswith('/revisions'))
        if scope['type']!='http' or scope['method']!='POST' or not (measurement or knowledge):
            return await self.app(scope,receive,send)
        received=0
        limit=BODY_LIMIT if measurement else 5*1024*1024+64*1024

        async def limited_receive():
            nonlocal received
            message=await receive()
            received+=len(message.get('body',b''))
            if received>limit:
                # Starlette closes temporary multipart files on MultiPartException.
                raise MultiPartException(LIMIT_MESSAGE if measurement else 'knowledge multipart body exceeds limit')
            return message

        await self.app(scope,limited_receive,send)
