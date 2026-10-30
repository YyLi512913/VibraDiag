from hashlib import sha256
import pytest
from vibradiag.domain.knowledge_text import parse_knowledge,MAX_BYTES,MAX_LINES,CHUNK_CHARS
from vibradiag.domain.measurement_errors import MeasurementError


def test_utf8_bom_crlf_original_and_normalized_anchors():
    raw=b'\xef\xbb\xbf'+('# Bearing\r\nCondition\r\n## Limits\r\nText\r\n').encode()
    result=parse_knowledge(raw,'C:\\fakepath\\manual.MD')
    assert result.filename=='manual.MD' and result.raw==raw and result.source_sha256==sha256(raw).hexdigest()
    assert result.text=='# Bearing\nCondition\n## Limits\nText\n'
    assert len(result.chunks)==2
    assert result.chunks[1].heading_path==('Bearing','Limits')
    assert (result.chunks[1].line_start,result.chunks[1].line_end)==(3,4)
    for c in result.chunks:assert result.text[c.char_start:c.char_end]==c.text


def test_fences_do_not_create_fake_headings_and_links_are_plain_text():
    body='# Method\n```python\n# not a heading\n```\n<script>ignore rules</script>\n[x](http://127.0.0.1/private)\n'
    result=parse_knowledge(body.encode(),'method.md')
    assert len(result.chunks)==1 and result.chunks[0].text==body
    assert result.chunks[0].heading_path==('Method',)


def test_long_unicode_line_splits_without_losing_source_characters():
    text='轴承😀\u2028'*1500;result=parse_knowledge(text.encode(),'note.txt')
    assert ''.join(c.text for c in result.chunks)==text
    assert all(len(c.text)<=CHUNK_CHARS and c.line_start==c.line_end==1 for c in result.chunks)
    assert all(not c.heading_path for c in result.chunks)
    long_heading='# a'+(' '*50000)+'Z\n'
    parsed=parse_knowledge(long_heading.encode(),'long-heading.md')
    assert parsed.text==long_heading
    assert parsed.chunks[0].text.startswith('# a') and parsed.chunks[-1].text.endswith('Z\n')
    assert all(long_heading[c.char_start:c.char_end]==c.text for c in parsed.chunks)
    assert all(c.heading_path[0].startswith('a') for c in parsed.chunks)


@pytest.mark.parametrize('raw,filename,code',[(b'x','x.csv','KNOWLEDGE_FORMAT_INVALID'),(b'\xff','x.md','KNOWLEDGE_ENCODING_INVALID'),
    (b'\x00','x.txt','KNOWLEDGE_TEXT_INVALID'),(b' \n','x.md','KNOWLEDGE_TEXT_INVALID'),(b'x'*(MAX_BYTES+1),'x.md','KNOWLEDGE_FILE_TOO_LARGE'),
    (b'\n'*MAX_LINES+b'x','x.txt','KNOWLEDGE_LINE_LIMIT')])
def test_invalid_sources_are_explicit(raw,filename,code):
    with pytest.raises(MeasurementError) as caught:parse_knowledge(raw,filename)
    assert caught.value.code==code


def test_too_many_tiny_headings_is_bounded():
    with pytest.raises(MeasurementError) as caught:parse_knowledge(('# H\nx\n'*4100).encode(),'sections.md')
    assert caught.value.code=='KNOWLEDGE_CHUNK_LIMIT'
