"""Regression for truncated CDN range responses observed during stage 0."""
import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'stage0_model', Path(__file__).resolve().parents[1] / 'scripts/prepare_real_development_model.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class Response(io.BytesIO):
    status = 206

    def __init__(self, body, content_range):
        super().__init__(body)
        self.headers = {'Content-Range': content_range}


def test_resume_multiple_bounded_ranges_verifies_whole_file(tmp_path, monkeypatch):
    data = b'abcdef012345'
    item = {'Path': 'test.safetensors', 'Size': len(data), 'Revision': 'fixed',
            'Sha256': hashlib.sha256(data).hexdigest()}
    (tmp_path / 'test.safetensors.part').write_bytes(data[:4])
    offsets = []

    def urlopen(request, timeout):
        offset = int(request.get_header('Range').split('=')[1].split('-')[0])
        offsets.append(offset)
        return Response(data[offset:offset + 4], f'bytes {offset}-{offset + 3}/{len(data)}')

    monkeypatch.setattr(model.urllib.request, 'urlopen', urlopen)
    model.download(item, tmp_path)
    assert offsets == [4, 8]
    assert (tmp_path / 'test.safetensors').read_bytes() == data
    assert not (tmp_path / 'test.safetensors.part').exists()


def test_wrong_cached_range_rejected_without_appending(tmp_path, monkeypatch):
    data = b'abcdef012345'
    item = {'Path': 'test.safetensors', 'Size': len(data), 'Revision': 'fixed',
            'Sha256': hashlib.sha256(data).hexdigest()}
    partial = tmp_path / 'test.safetensors.part'
    partial.write_bytes(data[:4])
    monkeypatch.setattr(model.urllib.request, 'urlopen',
                        lambda request, timeout: Response(data[:4], 'bytes 0-3/12'))
    with pytest.raises(ValueError, match='range mismatch'):
        model.download(item, tmp_path)
    assert partial.read_bytes() == data[:4]
    assert not (tmp_path / 'test.safetensors').exists()
