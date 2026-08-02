from abc import abstractmethod
from collections.abc import Sequence
import logging
import re
from typing import Any, cast

logger = logging.getLogger("satellite.server.security.authenticators")


class Authenticator:
    """Base class for objects that implement user authentication."""

    @abstractmethod
    def authenticate_with_password(self, username: str, password: str) -> bool:
        """Return whether the input combination (username, password) is valid."""
        raise NotImplementedError


class DictionaryAuthenticator(Authenticator):
    """Simple authenticator that uses a static dictionary of {username: password} to validate logins."""

    def __init__(self, users_to_passwords: dict[str, str]):
        super().__init__()

        self._user_db = users_to_passwords

    def authenticate_with_password(self, username: str, password: str) -> bool:
        """Assert whether the given 'password' is valid for the given 'username'."""
        return self._user_db.get(username) == password


class LDAPAuthenticator(Authenticator):
    """Authenticate users via querying LDAP resources."""

    DEFAULT_USER_VALIDATION_REGEX = r"""(?:([^=,]+=[^=,]+,?)+)|(?:[^=,]+)"""

    def __init__(
        self,
        server_address: str,
        server_port: int,
        use_ssl: bool = False,
        bind_dn_template: str = "{username}",
        user_name_regex: str = DEFAULT_USER_VALIDATION_REGEX,
        *,
        mock: bool = False,
        mock_entries: Sequence[tuple[str, str]] | None = None,
    ):
        super().__init__()

        from ldap3 import Server

        self._server = Server(server_address, port=server_port, use_ssl=use_ssl)

        self._bind_dn_template = bind_dn_template
        self._user_name_regex = re.compile(user_name_regex)

        self._mock = mock
        self._mock_entries: dict[str, dict[str, str]] = self._set_mock_entries(mock_entries)

    def _set_mock_entries(self, entries: Sequence[tuple[str, str]] | None) -> dict[str, dict[str, str]]:
        if entries is None:
            return {}

        mock_entries = {}
        for name, password in entries:
            distinguished_name = self._bind_dn_template.replace("{username}", name)

            mock_entries[distinguished_name] = {"userPassword": password}
        return mock_entries

    def _validate_user_name(self, user_name: str) -> bool:
        valid = self._user_name_regex.fullmatch(user_name) is not None

        if not valid:
            logger.debug(
                "Failed user name validation: name: '%s' regex: '%s'", user_name, self._user_name_regex.pattern
            )

        return valid

    def _create_connection(self, distinguished_name: str, password: str) -> Any:
        from ldap3 import Connection

        if self._mock:
            from ldap3 import MOCK_SYNC as STRATEGY
        else:
            from ldap3 import SYNC as STRATEGY

        connection = Connection(
            self._server,
            user=distinguished_name,
            password=password,
            read_only=not self._mock,
            raise_exceptions=True,
            client_strategy=STRATEGY,
        )

        if self._mock:
            from ldap3.strategy.mockSync import MockSyncStrategy

            for dn, entry in self._mock_entries.items():
                cast(MockSyncStrategy, connection.strategy).add_entry(dn, entry)

        return connection

    def authenticate_with_password(self, username: str, password: str) -> bool:
        """Establish a connection with the LDAP server and try authenticating with it."""
        from ldap3 import Connection
        from ldap3.core import exceptions as ldap_exc

        if not self._validate_user_name(username):
            return False

        distinguished_name = self._bind_dn_template.replace("{username}", username)

        connection: Connection = self._create_connection(distinguished_name, password)

        try:
            connection.bind()
        except ldap_exc.LDAPBindError as exc:
            logger.debug("Failed to authenticate user due to LDAP Bind error.", exc_info=exc)
            return False
        except ldap_exc.LDAPInvalidCredentialsResult as exc:
            logger.debug("Failed to authenticate user due to invalid credentials.", exc_info=exc)
            return False
        except ldap_exc.LDAPException as exc:
            logger.warning("Failed to authenticate user due to unknown LDAP error.", exc_info=exc)
            return False

        connection.unbind()

        return True
