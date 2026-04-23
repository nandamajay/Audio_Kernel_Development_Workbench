from requests.exceptions import SSLError

from app.services.env_service import validate_qgenie_key


class DummyResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_validate_qgenie_key_uses_custom_ca_bundle(tmp_path, mocker):
    ca_bundle = tmp_path / "qcom.crt"
    ca_bundle.write_text("dummy", encoding="utf-8")

    get_mock = mocker.patch("app.services.env_service.requests.get", return_value=DummyResponse(200, "ok"))

    ok, message = validate_qgenie_key(
        api_key="token",
        provider_url="https://qgenie-chat.qualcomm.com/v1",
        ssl_verify="true",
        ca_bundle=str(ca_bundle),
    )

    assert ok is True
    assert "validated" in message.lower()
    assert get_mock.call_args.kwargs["verify"] == str(ca_bundle)


def test_validate_qgenie_key_rejects_missing_ca_bundle(mocker):
    mocker.patch("app.services.env_service.requests.get")

    ok, message = validate_qgenie_key(
        api_key="token",
        provider_url="https://qgenie-chat.qualcomm.com/v1",
        ssl_verify="true",
        ca_bundle="/missing/ca.crt",
    )

    assert ok is False
    assert "CA bundle path not found" in message


def test_validate_qgenie_key_allows_ssl_disabled(mocker):
    get_mock = mocker.patch("app.services.env_service.requests.get", return_value=DummyResponse(200, "ok"))

    ok, _ = validate_qgenie_key(
        api_key="token",
        provider_url="https://qgenie-chat.qualcomm.com/v1",
        ssl_verify="false",
    )

    assert ok is True
    assert get_mock.call_args.kwargs["verify"] is False


def test_validate_qgenie_key_ssl_error_message(mocker):
    mocker.patch("app.services.env_service.requests.get", side_effect=SSLError("bad cert"))

    ok, message = validate_qgenie_key(
        api_key="token",
        provider_url="https://qgenie-chat.qualcomm.com/v1",
        ssl_verify="true",
    )

    assert ok is False
    assert "QGENIE_CA_BUNDLE" in message
