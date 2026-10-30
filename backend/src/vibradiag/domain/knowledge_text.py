"""Deterministic plain-text MD/TXT anchors. No rendering, URL fetching or code execution."""
from dataclasses import dataclass
from hashlib import sha256
import re
from vibradiag.domain.measurement_errors import MeasurementError

MAX_BYTES=5*1024*1024
MAX_LINES=200000
MAX_CHUNKS=4096
CHUNK_CHARS=1800
POLICY='knowledge-lines-v1'


@dataclass(frozen=True)
class Chunk:
    text: str
    heading_path: tuple[str,...]
    line_start: int
    line_end: int
    char_start: int
    char_end: int


@dataclass(frozen=True)
class KnowledgeText:
    filename: str
    format: str
    raw: bytes
    text: str
    source_sha256: str
    text_sha256: str
    chunks: tuple[Chunk,...]


def parse_knowledge(raw,filename):
    filename=(filename or '').replace('\\','/').rsplit('/',1)[-1]
    if not 1<=len(filename)<=160 or any(ord(c)<32 for c in filename) or not filename.lower().endswith(('.md','.txt')):
        raise MeasurementError('KNOWLEDGE_FORMAT_INVALID','Upload a named UTF-8 MD/TXT file',status=422)
    if len(raw)>MAX_BYTES:raise MeasurementError('KNOWLEDGE_FILE_TOO_LARGE','Knowledge file exceeds 5 MiB',status=413)
    try:text=raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
    except UnicodeDecodeError:raise MeasurementError('KNOWLEDGE_ENCODING_INVALID','UTF-8 knowledge text required',status=422) from None
    if not text.strip() or any(ord(c)<32 and c not in '\t\n' for c in text):
        raise MeasurementError('KNOWLEDGE_TEXT_INVALID','Nonempty text without binary controls required',status=422)
    if text.count('\n')+1>MAX_LINES:raise MeasurementError('KNOWLEDGE_LINE_LIMIT','Knowledge line count exceeds 200000',status=422)
    chunks=[];parts=[];length=0;start=0;end=0;first_line=1;last_line=1;path=[];chunk_path=();offset=0;fence=None
    def flush():
        nonlocal parts,length
        value=''.join(parts)
        if value.strip():chunks.append(Chunk(value,chunk_path,first_line,last_line,start,end))
        parts=[];length=0
        if len(chunks)>MAX_CHUNKS:raise MeasurementError('KNOWLEDGE_CHUNK_LIMIT','Knowledge file creates too many chunks',status=422)
    markdown=filename.lower().endswith('.md')
    for line_no,match in enumerate(re.finditer(r'[^\n]*\n|[^\n]+$',text),1):
        line=match[0]
        marker=re.match(r'^ {0,3}(`{3,}|~{3,})',line) if markdown else None
        heading=re.match(r'^ {0,3}(#{1,6})[ \t]+',line) if markdown and not fence and not marker else None
        label=line[heading.end():].strip().rstrip('#').rstrip()[:256] if heading else ''
        if not label:heading=None
        if heading:
            flush();level=len(heading[1])
            path=[p for p in path if p[0]<level]+[(level,label)]
        if marker:
            token=marker[1]
            if fence is None:fence=token
            elif token[0]==fence[0] and len(token)>=len(fence):fence=None
        cursor=0
        while cursor<len(line):
            if not parts:start=offset+cursor;first_line=line_no;chunk_path=tuple(p[1] for p in path)
            size=min(CHUNK_CHARS-length,len(line)-cursor)
            parts.append(line[cursor:cursor+size]);length+=size;cursor+=size;end=offset+cursor;last_line=line_no
            if length==CHUNK_CHARS:flush()
        offset+=len(line)
    flush()
    return KnowledgeText(filename,'md' if markdown else 'txt',raw,text,sha256(raw).hexdigest(),sha256(text.encode()).hexdigest(),tuple(chunks))
