from unittest.mock import patch
from types import SimpleNamespace
import pytest
from printing3d.video.verify import verify_mp4


def test_valid_mp4_metadata(tmp_path):
    file = tmp_path / "video.mp4"
    file.write_bytes(b"sample")
    metadata = '{"streams":[{"codec_type":"video","codec_name":"h264","width":720,"height":1280}],"format":{"duration":"10.0"}}'
    with patch("printing3d.video.verify.subprocess.run", return_value=SimpleNamespace(stdout=metadata)) as run:
        assert verify_mp4(file, "vertical", 10)["codec"] == "h264"
        assert run.call_count == 2


def test_reject_wrong_codec(tmp_path):
    file = tmp_path / "video.mp4"
    file.write_bytes(b"sample")
    metadata = '{"streams":[{"codec_type":"video","codec_name":"vp9","width":720,"height":1280}],"format":{"duration":"10.0"}}'
    with patch("printing3d.video.verify.subprocess.run", return_value=SimpleNamespace(stdout=metadata)):
        with pytest.raises(ValueError, match="codec"):
            verify_mp4(file, "vertical", 10)
