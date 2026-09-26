import pytest

from shared.urlsafe import UnsafeURLError, check_public_url


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost:6379/",
    "http://127.0.0.1/",
    "http://10.0.0.5/",
    "file:///etc/passwd",
    "gopher://example.com/",
])
def test_rejects_private_and_non_http(url):
    with pytest.raises(UnsafeURLError):
        check_public_url(url)


async def test_article_fetch_blocks_metadata_endpoint():
    from core.narrative.extract import _fetch

    with pytest.raises(UnsafeURLError):
        await _fetch("http://169.254.169.254/latest/meta-data/")
