import httpx
import pytest
import yaml

from satellite.server.configuration import ManagerConfiguration
from satellite.server.security import authenticate_dependencies


def test_no_configuration(default_configuration_setup):
    dependencies = authenticate_dependencies()
    assert len(dependencies) == 0


class TestAPIKey:
    @pytest.fixture(autouse=True)
    def _configuration(self, tmp_path, monkeypatch):
        configuration = ManagerConfiguration()
        configuration.authentication.secret_keys = ["secret_test_key-123", "secret_test_key-456"]

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    async def test_with_configuration_block_request(self, client: httpx.AsyncClient):
        dependencies = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/ping")
        assert response.status_code == 401

    async def test_with_configuration_and_header_api_key_allow_request(self, client: httpx.AsyncClient):
        dependencies = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/ping", headers={"x-api-key": "secret_test_key-123"})
        assert response.status_code == 200

        response = await client.get("/ping", headers={"x-api-key": "invalid"})
        assert response.status_code == 401

        response = await client.get("/ping", headers={"x-api-key": "secret_test_key-456"})
        assert response.status_code == 200

    async def test_with_configuration_and_query_api_key_allow_request(self, client: httpx.AsyncClient):
        dependencies = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/ping?api-key=secret_test_key-123")
        assert response.status_code == 200

        response = await client.get("/ping?api-key=invalid")
        assert response.status_code == 401

        response = await client.get("/ping?api-key=secret_test_key-456")
        assert response.status_code == 200


class TestAPIKeyAnonymousAccess:
    @pytest.fixture(autouse=True)
    def _configuration(self, tmp_path, monkeypatch):
        configuration = ManagerConfiguration()
        configuration.authentication.secret_keys = ["secret_test_key-123", "secret_test_key-456"]
        configuration.authentication.allow_anonymous_access = True

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    async def test_with_configuration_api_key_and_annonymous_access_allow_request(self, client: httpx.AsyncClient):
        dependencies = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/ping", headers={"x-api-key": "secret_test_key-123"})
        assert response.status_code == 200

        response = await client.get("/ping", headers={"x-api-key": "invalid"})
        assert response.status_code == 200

        response = await client.get("/ping", headers={"x-api-key": "secret_test_key-456"})
        assert response.status_code == 200
