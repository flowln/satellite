import asyncio
import time as ttime

import httpx
import jwt
import pytest
import yaml

from satellite.models import SuccessfulLoginResponse, UserInformation
from satellite.server.configuration import ManagerConfiguration, _ManagerAuthenticationProvider
from satellite.server.security import (
    ANONYMOUS_USER_NAME,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
)
from satellite.server.security.access_policies import BasicAPIAccessPolicy


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
        response = await client.get("/status")
        assert response.status_code == 401

    async def test_with_configuration_and_header_api_key_allow_request(self, client: httpx.AsyncClient):
        response = await client.get("/status", headers={"x-api-key": "secret_test_key-123"})
        assert response.status_code == 200, response.json()

        response = await client.get("/status", headers={"x-api-key": "invalid"})
        assert response.status_code == 401

        response = await client.get("/status", headers={"x-api-key": "secret_test_key-456"})
        assert response.status_code == 200, response.json()

    async def test_with_configuration_and_query_api_key_allow_request(self, client: httpx.AsyncClient):
        response = await client.get("/status?api-key=secret_test_key-123")
        assert response.status_code == 200, response.json()

        response = await client.get("/status?api-key=invalid")
        assert response.status_code == 401

        response = await client.get("/status?api-key=secret_test_key-456")
        assert response.status_code == 200, response.json()


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
        response = await client.get("/status", headers={"x-api-key": "secret_test_key-123"})
        assert response.status_code == 200, response.json()

        response = await client.get("/status", headers={"x-api-key": "invalid"})
        assert response.status_code == 200, response.json()

        response = await client.get("/status", headers={"x-api-key": "secret_test_key-456"})
        assert response.status_code == 200, response.json()


class TestAuthenticator:
    @pytest.fixture
    def configuration_dictionary(self, tmp_path, monkeypatch):
        configuration = ManagerConfiguration()
        configuration.network.use_mocked_backend = True
        provider = _ManagerAuthenticationProvider(
            provider="test",
            expiration_time=10.0,
            authenticator="satellite.server.security.authenticators:DictionaryAuthenticator",
            args={"users_to_passwords": {"ed": "123", "molly": "456"}},
        )
        configuration.authentication.providers = [provider]
        configuration.authorization.api_access_authorization.args = {
            "roles": BasicAPIAccessPolicy.get_roles_for_admin_power("ed", "molly")
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    @pytest.fixture
    def configuration_ldap(self, tmp_path, monkeypatch):
        pytest.importorskip("ldap3")

        configuration = ManagerConfiguration()
        configuration.network.use_mocked_backend = True
        provider = _ManagerAuthenticationProvider(
            provider="test",
            expiration_time=10.0,
            authenticator="satellite.server.security.authenticators:LDAPAuthenticator",
            args={
                "server_address": "test_addr",
                "server_port": 1234,
                "bind_dn_template": "uid={username}",
                "mock": True,
                "mock_entries": (("ed", "123"), ("molly", "456")),
            },
        )
        configuration.authentication.providers = [provider]
        configuration.authorization.api_access_authorization.args = {
            "roles": BasicAPIAccessPolicy.get_roles_for_admin_power("ed", "molly")
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    @pytest.fixture(autouse=True, params=[configuration_dictionary, configuration_ldap])
    def configuration(self, request):
        request.getfixturevalue(request.param.__name__)
        yield

    async def test_with_configuration_block_request(self, client: httpx.AsyncClient):
        response = await client.get("/status")
        assert response.status_code == 401

    async def test_with_configuration_wrong_password(self, client: httpx.AsyncClient):
        response = await client.post("/login", data={"username": "ed", "password": "wrong"})
        assert response.status_code == 400

    async def test_with_configuration_wrong_user(self, client: httpx.AsyncClient):
        response = await client.post("/login", data={"username": "sophie", "password": "wrong"})
        assert response.status_code == 400

    async def test_with_configuration_accept_request(self, client: httpx.AsyncClient):
        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200, response.json()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"
        assert parsed_response.expires_in == 10

        jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="ed")

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

        response = await client.post("/login", data={"username": "molly", "password": "123"})
        assert response.status_code == 400

        response = await client.post("/login", data={"username": "molly", "password": "456"})
        assert response.status_code == 200, response.json()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="molly")

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

    async def test_with_configuration_token_invalid_signature(self, client: httpx.AsyncClient):
        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200, response.json()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="ed")

        parsed_response.token = parsed_response.token[:-1] + ("0" if parsed_response.token[-1] != "0" else "1")
        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 401

    async def test_with_configuration_token_expiration(self, client: httpx.AsyncClient):
        response = await client.post("/login?override_expiration_time=2", data={"username": "ed", "password": "123"})
        assert response.status_code == 200, response.json()
        _return_time = ttime.time()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"
        assert parsed_response.expires_in == 2

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

        claims = jwt.decode(parsed_response.token, key=JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], subject="ed")
        assert _return_time + parsed_response.expires_in == pytest.approx(claims["exp"], abs=1)

        await asyncio.sleep(parsed_response.expires_in)

        assert ttime.time() > claims["exp"]

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 401

    async def test_with_configuration_token_revoke(self, client: httpx.AsyncClient):
        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200, response.json()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

        response = await client.post("/logout", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 401

    async def test_with_configuration_token_revoke_refresh(self, client: httpx.AsyncClient):
        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200, response.json()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

        response = await client.post("/logout", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 401

        response = await client.post("/logout", headers={"Authorization": f"Bearer {parsed_response.refresh_token}"})
        assert response.status_code == 200, response.json()

        new_tokens_response = await client.post(
            "/session_refresh", headers={"Authorization": f"Bearer {parsed_response.refresh_token}"}
        )
        assert new_tokens_response.status_code == 401

    async def test_with_configuration_token_refresh(self, client: httpx.AsyncClient):
        response = await client.post("/login?override_expiration_time=2", data={"username": "ed", "password": "123"})
        assert response.status_code == 200, response.json()
        _return_time = ttime.time()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"
        assert parsed_response.expires_in == 2

        response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

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
        assert response.status_code == 200, response.json()

        # Assert the old refresh token doesn't work anymore
        new_tokens_response = await client.post(
            "/session_refresh", headers={"Authorization": f"Bearer {parsed_response.refresh_token}"}
        )
        assert new_tokens_response.status_code == 401

    async def test_with_configuration_whoami(self, client: httpx.AsyncClient):
        response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert response.status_code == 200, response.json()

        parsed_response = SuccessfulLoginResponse.model_validate(response.json())
        assert parsed_response.token_type == "bearer"

        response = await client.get("/whoami", headers={"Authorization": f"Bearer {parsed_response.token}"})
        assert response.status_code == 200, response.json()

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
        response = await client.get("/status")
        assert response.status_code == 200, response.json()

        response = await client.get("/whoami")
        assert response.status_code == 200, response.json()

        parsed_response = UserInformation.model_validate(response.json())
        assert parsed_response.user_name == ANONYMOUS_USER_NAME


class TestBasicAuthorization:
    @pytest.fixture(autouse=True)
    def _configuration(self, tmp_path, monkeypatch):
        configuration = ManagerConfiguration()
        configuration.network.use_mocked_backend = True
        provider = _ManagerAuthenticationProvider(
            provider="test",
            expiration_time=10.0,
            authenticator="satellite.server.security.authenticators:LDAPAuthenticator",
            args={
                "server_address": "test_addr",
                "server_port": 1234,
                "bind_dn_template": "uid={username}",
                "mock": True,
                "mock_entries": (("ed", "123"), ("molly", "456")),
            },
        )
        configuration.authentication.providers = [provider]
        configuration.authorization.api_access_authorization.args = {
            "roles": {"ed": {"scopes_add": ["read:status"]}, "molly": {"scopes_add": ["read:history"]}}
        }

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as _file:
            yaml.safe_dump(configuration.model_dump(), stream=_file)

        monkeypatch.setenv("QSERVER_CONFIG", str(config_path))

        yield

    async def test_whoami_with_scopes(self, client: httpx.AsyncClient):
        ed_response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert ed_response.status_code == 200, ed_response.json()

        parsed_ed_response = SuccessfulLoginResponse.model_validate(ed_response.json())

        molly_response = await client.post("/login", data={"username": "molly", "password": "456"})
        assert molly_response.status_code == 200, molly_response.json()

        parsed_molly_response = SuccessfulLoginResponse.model_validate(molly_response.json())

        ed_response = await client.get("/whoami", headers={"Authorization": f"Bearer {parsed_ed_response.token}"})
        assert ed_response.status_code == 200, ed_response.json()

        ed_whoami = UserInformation.model_validate(ed_response.json())
        assert ed_whoami.scopes == ["read:status"]

        molly_response = await client.get("/whoami", headers={"Authorization": f"Bearer {parsed_molly_response.token}"})
        assert molly_response.status_code == 200, molly_response.json()

        molly_whoami = UserInformation.model_validate(molly_response.json())
        assert molly_whoami.scopes == ["read:history"]

    async def test_read_scope_validate(self, client: httpx.AsyncClient):
        ed_response = await client.post("/login", data={"username": "ed", "password": "123"})
        assert ed_response.status_code == 200, ed_response.json()

        parsed_ed_response = SuccessfulLoginResponse.model_validate(ed_response.json())

        molly_response = await client.post("/login", data={"username": "molly", "password": "456"})
        assert molly_response.status_code == 200, molly_response.json()

        parsed_molly_response = SuccessfulLoginResponse.model_validate(molly_response.json())

        ed_response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_ed_response.token}"})
        assert ed_response.status_code == 200, ed_response.json()
        ed_response = await client.get("/history_get", headers={"Authorization": f"Bearer {parsed_ed_response.token}"})
        assert ed_response.status_code == 401

        molly_response = await client.get("/status", headers={"Authorization": f"Bearer {parsed_molly_response.token}"})
        assert molly_response.status_code == 401
        molly_response = await client.get(
            "/history_get", headers={"Authorization": f"Bearer {parsed_molly_response.token}"}
        )
        assert molly_response.status_code == 200, molly_response.json()
