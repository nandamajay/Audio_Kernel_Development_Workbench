from app.utils.driver_link_fetcher import DriverLinkFetcher
from app.utils.upstream_converter_prompt import build_conversion_prompt
from app.models import db
from sqlalchemy import inspect


def test_detect_link_type_gerrit():
    fetcher = DriverLinkFetcher(ssl_verify=False)
    assert fetcher.detect_link_type("https://gerrit.qualcomm.com/c/kernel/msm/+/12345") == "gerrit"


def test_detect_link_type_grok():
    fetcher = DriverLinkFetcher(ssl_verify=False)
    assert fetcher.detect_link_type("https://grok.qualcomm.com/xref/qssi/+/sound/soc/codecs/wcd937x.c") == "grok"


def test_detect_link_type_go():
    fetcher = DriverLinkFetcher(ssl_verify=False)
    assert fetcher.detect_link_type("https://go/akdw-converter") == "go_link"


def test_detect_link_type_lore():
    fetcher = DriverLinkFetcher(ssl_verify=False)
    assert fetcher.detect_link_type("https://lore.kernel.org/all/123@lore.kernel.org/") == "lore"


def test_detect_link_type_github():
    fetcher = DriverLinkFetcher(ssl_verify=False)
    assert fetcher.detect_link_type("https://github.com/torvalds/linux/blob/master/README") == "github"


def test_fetch_link_invalid_url(client):
    res = client.post("/api/converter/fetch-link", json={"url": "https://example.com/driver"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False


def test_fetch_link_404(monkeypatch, client):
    class StubResp:
        status_code = 404
        text = ""
        url = "https://example.com/driver.c"

    def _stub_get(self, url, **kwargs):
        return StubResp()

    monkeypatch.setattr(DriverLinkFetcher, "_get", _stub_get)
    res = client.post("/api/converter/fetch-link", json={"url": "https://example.com/driver.c"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "failed" in (data.get("error") or "").lower() or "404" in (data.get("error") or "")


def test_convert_endpoint_exists(client):
    res = client.post(
        "/api/converter/convert",
        json={"source_code": "int main(){}", "filename": "drv.c", "metadata": {}},
    )
    assert res.status_code == 200


def test_converter_jobs_table_exists(client, app):
    client.get("/api/converter/jobs")
    with app.app_context():
        insp = inspect(db.engine)
        assert insp.has_table("converter_jobs")


def test_converter_jobs_returns_list(client):
    res = client.get("/api/converter/jobs")
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list)


def test_build_conversion_prompt_basic():
    messages = build_conversion_prompt({"source_code": "int main(){}", "filename": "drv.c"})
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_conversion_prompt_requirements():
    messages = build_conversion_prompt({"source_code": "int main(){}", "filename": "drv.c", "requirements": "Keep DT bindings."})
    assert "Keep DT bindings" in messages[1]["content"]


def test_build_conversion_prompt_missing_metadata():
    messages = build_conversion_prompt({"source_code": "int main(){}", "filename": "drv.c", "metadata": {}})
    assert "CL Number" in messages[1]["content"]


def test_settings_api_contains_ssl_keys(client):
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.get_json()
    assert "qgenie_ssl_verify" in data
    assert "qgenie_ca_bundle" in data


def test_settings_save_roundtrip_ssl(client):
    res = client.post("/api/settings/save", json={"ssl_verify": "false", "ca_bundle": ""})
    assert res.status_code == 200
    res2 = client.get("/api/settings")
    data = res2.get_json()
    assert data["qgenie_ssl_verify"] == "false"


def test_converter_template_elements():
    with open("app/templates/converter.html", "r", encoding="utf-8") as handle:
        html = handle.read()
    assert "Link Mode" in html
    assert "requirements" in html
    assert "metadataCard" in html


def test_dashboard_stats_has_drivers_converted(client):
    res = client.get("/api/dashboard/stats")
    assert res.status_code == 200
    data = res.get_json()
    assert "drivers_converted" in data
