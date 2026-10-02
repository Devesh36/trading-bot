import pytest
from config import Config


def test_defaults_and_gates():
    assert Config().trading_mode == "paper"
    assert Config().credentials == ("", "")
    for kwargs in (
        {"trading_mode": "live"},
        {"max_leverage": 11},
        {"enable_pyramiding": True},
        {"use_volume_filter": True},
    ):
        with pytest.raises(ValueError):
            Config(**kwargs)


def test_secrets_only_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_TESTNET_API_KEY", "wrong")
    p = tmp_path / "env"
    p.write_text(
        "TRADING_MODE=testnet\nDELTA_TESTNET_API_KEY=demo\nDELTA_TESTNET_API_SECRET=secret\n"
    )
    c = Config.load(p)
    assert c.credentials == ("demo", "secret")
    assert "testnet" in c.base_url
