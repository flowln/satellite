from abc import abstractmethod


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
