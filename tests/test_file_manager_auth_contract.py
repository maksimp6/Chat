from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_file_manager_resolves_provider_auth_before_requests():
    source = (ROOT / "file_manager.py").read_text(encoding="utf-8")
    assert "from yandex_client_modules.request_mixin import _resolve_global_provider_credential" in source
    marker = "def _fm_request(self, method, url, **kwargs):"
    start = source.index(marker)
    section = source[start:source.index("\n    def _fm_handle_error", start)]
    assert "_resolve_global_provider_credential(self)" in section
    assert section.index("_resolve_global_provider_credential(self)") < section.index("self.session.request(")
