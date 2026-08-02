import asyncio
import time as ttime

import httpx
import jwt
import pytest
import yaml

from satellite.models import SuccessfulLoginResponse, UserInformation
from satellite.server.configuration import ManagerConfiguration, _ManagerAuthenticationProvider
from satellite.server.security import ANONYMOUS_USER_NAME, JWT_ALGORITHM, JWT_SECRET_KEY, authenticate_dependencies


def test_no_configuration(default_configuration_setup):
    dependencies, _ = authenticate_dependencies()
    assert len(dependencies) == 0


class TestAPIKey:
    @pytest.fixture(autouse=True)
    def _configuration(self, tmp_path, monkeypatch):
        configuration = ManagerConfiguration()
        configuration.network.use_mocked_backend = True
        configuration.authentication.secret_keys = ["secret_test_key-123", "secret_test_key-456"]

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    async def test_with_configuration_block_request(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/status")
        assert response.status_code == 401

    async def test_with_configuration_and_header_api_key_allow_request(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/status", headers={"x-api-key": "secret_test_key-123"})
        assert response.status_code == 200

        response = await client.get("/status", headers={"x-api-key": "invalid"})
        assert response.status_code == 401

        response = await client.get("/status", headers={"x-api-key": "secret_test_key-456"})
        assert response.status_code == 200

    async def test_with_configuration_and_query_api_key_allow_request(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/status?api-key=secret_test_key-123")
        assert response.status_code == 200

        response = await client.get("/status?api-key=invalid")
        assert response.status_code == 401

        response = await client.get("/status?api-key=secret_test_key-456")
        assert response.status_code == 200


class TestAPIKeyAnonymousAccess:
    @pytest.fixture(autouse=True)
    def _configuration(self, tmp_path, monkeypatch):
        configuration = ManagerConfiguration()
        configuration.network.use_mocked_backend = True
        configuration.authentication.secret_keys = ["secret_test_key-123", "secret_test_key-456"]
        configuration.authentication.allow_anonymous_access = True

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    async def test_with_configuration_api_key_and_annonymous_access_allow_request(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/status", headers={"x-api-key": "secret_test_key-123"})
        assert response.status_code == 200

        response = await client.get("/status", headers={"x-api-key": "invalid"})
        assert response.status_code == 200

        response = await client.get("/status", headers={"x-api-key": "secret_test_key-456"})
        assert response.status_code == 200


class TestDictionary:
    @pytest.fixture(autouse=True)
    def _configuration(self, tmp_path, monkeypatch):
        configuration = ManagerConfiguration()
        configuration.network.use_mocked_backend = True
        provider = _ManagerAuthenticationProvider(
            provider="test",
            expiration_time=2.0,
            authenticator="satellite.server.security.authenticators:DictionaryAuthenticator",
            args={"users_to_passwords": {"ed": "123", "molly": "456"}},
        )
        configuration.authentication.providers = [provider]

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    async def test_with_configuration_block_request(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/status")
        assert response.status_code == 401

    async def test_with_configuration_wrong_password(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.post("/login", data={"username": "ed", "password": "wrong"})
        assert response.status_code == 400

    async def test_with_configuration_wrong_user(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.post("/login", data={"username": "sophie", "password": "wrong"})
        assert response.status_code == 400

    async def test_with_configuration_accept_request(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="ed")

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200

        response = await client.post("/login", data={"username": "molly", "password": "123"})
        assert response.status_code == 400

        response = await client.post("/login", data={"username": "molly", "password": "456"})
        assert response.status_code == 200

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="molly")

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200

    async def test_with_configuration_token_invalid_signature(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="ed")

        parsed_response.token = parsed_response.token[:-1] + parsed_response.token[-1].swapcase()
        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 401

    async def test_with_configuration_token_expiration(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200
        _return_time = ttime.time()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200

        claims = jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="ed")
        assert _return_time + parsed_response.expires_in == pytest.approx(claims["exp"], abs=1)

        await asyncio.sleep(parsed_response.expires_in)

        assert ttime.time() > claims["exp"]

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 401

    async def test_with_configuration_token_refresh(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200
        _return_time = ttime.time()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200

        claims = jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="ed")
        assert _return_time + parsed_response.expires_in == pytest.approx(claims["exp"], abs=1)

        await asyncio.sleep(parsed_response.expires_in)

        assert ttime.time() > claims["exp"]

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 401

        new_tokens_response = await client.post(
            "/session_refresh", headers={"Authorization": f"Bearer {parsed_response.refresh_token}"}
        )
        assert new_tokens_response.status_code == 200

        new_tokens = SuccessfulLoginResponse.model_validate(new_tokens_response.json())

        # Assert the old token remains expired
        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 401

        response = await client.get("/status", headers={"Authorization": f"Bearer {new_tokens.token}"})
        assert response.status_code == 200

    async def test_with_configuration_whoami(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        response = await client.get("/whoami", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200

        parsed_response = UserInformation.model_validate(response.json())
        assert parsed_response.user_name == "ed"


class TestDictionaryAnonymousAccess:
    @pytest.fixture(autouse=True)
    def _configuration(self, tmp_path, monkeypatch):
        configuration = ManagerConfiguration()
        configuration.network.use_mocked_backend = True
        provider = _ManagerAuthenticationProvider(
            provider="test",
            expiration_time=1,
            authenticator="satellite.server.security.authenticators:DictionaryAuthenticator",
            args={"users_to_passwords": {"ed": "123", "molly": "456"}},
        )
        configuration.authentication.providers = [provider]
        configuration.authentication.allow_anonymous_access = True

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    async def test_with_configuration_allow_request_anonymous_user(self, client: httpx.AsyncClient):
        dependencies, _ = authenticate_dependencies()
        assert len(dependencies) == 1

        response = await client.get("/status")
        assert response.status_code == 200

        response = await client.get("/whoami")
        assert response.status_code == 200

        parsed_response = UserInformation.model_validate(response.json())
        assert parsed_response.user_name == ANONYMOUS_USER_NAME
