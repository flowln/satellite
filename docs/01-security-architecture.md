# Security architecture

## Authentication

For the authentication of users, the following is a rough schematic of how the code is laid out:

```mermaid
---
config:
    htmlLabels: true
    markdownAutoWrap: false
---
flowchart TD
    entrypoint["`Entrypoint <br> <code>_create_app</code>`"]
    authDependencies["`authenticate_dependencies`"]

    entrypoint --> authDependencies
    entrypoint <--> app["Application routes"]

    subgraph satellite.server.security
        securityConfig["`Check configured options in <br> <code>configuration.authentication</code>`"]@{ shape: hex }
        authDependencies --> securityConfig

        securityConfig -->|has 'secret_keys'| apiKeyCheck["`_check_api_key`"]
        securityConfig -->|has 'providers'| oauthCheck["`_create_password_login_handler`"]

        oauthCheck --> oauthRoutes["Login routes"]
    end

    oauthRoutes --> app

    subgraph satellite.server.security.authenticators
        direction BT

        oauthRoutes <--> dictAuth["DictionaryAuthenticator"]@{ shape: stadium }
        oauthRoutes <--> ldapAuth["LDAPAuthenticator"]@{ shape: stadium }

        authBase["Authenticator"]
        authBase --> dictAuth
        authBase --> ldapAuth
    end

    security["FastAPI Dependency"]

    apiKeyCheck --- security
    oauthCheck --- security

    security --> app
```

For the `API Key` method, a simple [`Dependency`](https://fastapi.tiangolo.com/tutorial/dependencies/) function is created, which checks the request headers for
a valid API Key, and if none is found, either an exception is raised, or `False` is returned (when anonymous access is allowed).

For the `Providers`, currently the only method implemented is `password`, meaning the user sends in a **user name** and a **password** for authentication.
This is implemented with the [`OAuth2PasswordBearer`](https://fastapi.tiangolo.com/reference/security/#fastapi.security.OAuth2PasswordBearer) dependency, and
the [OAuth2PasswordRequestForm](https://fastapi.tiangolo.com/reference/security/#fastapi.security.OAuth2PasswordRequestForm) dependency for retrieving the
necessary information in the `login` endpoint.

On the `Providers` implementation, the configuration associated with a provider includes instructions to load a class that will actually authenticate the user.
This class must be a subclass of [`Authenticator`](../src/satellite/server/security/authenticators.py), and can communicate with other services to ensure the
provided credentials are valid.

For all the token-based authentication methods, a successful authentication returns to the user a `access_token` and a `refresh_token`, both of which are
[JWT](https://www.jwt.io/introduction#what-is-json-web-token) tokens. These allow the user to access resources on the server until they expire, by adding them
into the request header. The verification of the token's validity is done in the `_get_decoded_token` and `get_current_user` functions on the security module,
which are then added to the normal API Routes as Dependencies.

In order to revoke tokens before they expire, a dictionary `JWT_BLACKLIST` is kept in the global scope of the security module. It is keyed by the provider name,
and its value is a set of blacklisted tokens. This set is verified on every operation to ensure a revoked token doesn't succeed in authenticating.

#### Client implementation

On the client side, the authentication feature of [`httpx`](https://www.python-httpx.org/advanced/authentication/) is used. In `client.py`, a `httpx.Auth` subclass
is created to handle adding the proper HTTP headers for authentication. If an access token has expired, an attempt is made to refresh it using the refresh token,
making the token handling transparent to the user.

## Authorization

For the authorization portion of security, there's a distinction made between **API access authorization** and **resource access authorization**.

Preveing unauthorized access to API endpoints is made using the [OAuth2 scope mechanism](https://fastapi.tiangolo.com/advanced/security/oauth2-scopes/),
easily made available by FastAPI. So, each endpoint has their required scopes declared when defining the endpoint itself, and these scopes are associated
with the JWT authentication token (by adding the user scopes to the token itself as a claim) or the user of the API key. These user scopes are requested from
the authorization providers, called **access policies**. Each policy has its own behavior for defining the scopes of each user, but all of them in essence do
the same job: give the set of API access scopes a particular user has access to.

In contrast, limiting resource access is more limited. Instead of attaching individual scopes for each user, for resource access we join users into groups,
each of which will have certain permissions on individual resources. For each group, the default is to allow access to all resources, but you can add or remove
access to specific ones either by using regular expressions, or by directly stating the name of the resource. This configuration is independent from the access
policy, and resides in the `group_permissions` configuration option in the `authorization` section.
