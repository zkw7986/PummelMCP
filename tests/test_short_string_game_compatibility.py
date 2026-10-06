import pytest
from pummelmcp.pmh.schemas import Utf8String, SchemaValueError

@pytest.mark.parametrize('value', ['a'*127, '中'*42+'a', '中文\nEnglish'])
def test_short_strings_match_binary_reader(value):
    raw=Utf8String.encode(value)
    assert raw[0]&128==0
    assert raw[0]==len(raw[1:])
    assert raw[1:].decode('utf-8')==value

@pytest.mark.parametrize('value', ['a'*128, '中'*43])
def test_refuse_invalid_single_byte_dotnet_lengths(value):
    with pytest.raises(SchemaValueError):
        Utf8String.encode(value)
