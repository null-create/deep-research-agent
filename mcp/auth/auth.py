"""
Simple JWT authentication handler for MCP.

The Research Assistant will be configured with the API key needed to access the MCP API.
This handler will be responsible for generating and refreshing JWT tokens as needed, ensuring
that the Research Assistant can authenticate with the MCP API securely.

"""

import jwt
import time

from mcp.server.auth.provider import TokenVerifier, AccessToken


class JWTAuthHandler(TokenVerifier):
    def __init__(self, api_key: str, secret_key: str, algorithm: str = "HS256"):
        self.api_key = api_key
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.token = None
        self.token_expiry = 0

    def generate_token(self) -> None:
        """Generates a new JWT token."""
        payload = {
            "api_key": self.api_key,
            "exp": time.time() + 3600,  # Token expires in 1 hour
        }
        self.token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        self.token_expiry = payload["exp"]

    def get_token(self) -> AccessToken:
        """Returns a valid JWT token, refreshing it if necessary."""
        if not self.token or time.time() >= self.token_expiry:
            self.generate_token()
        return AccessToken(self.token)

    def refresh_token(self) -> None:
        """Refreshes the JWT token."""
        self.generate_token()

    def validate_token(self, token: str) -> bool:
        """Validates the provided JWT token."""
        try:
            decoded = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return decoded.get("api_key") == self.api_key
        except jwt.ExpiredSignatureError:
            print("Token has expired.")
            return False
        except jwt.InvalidTokenError:
            print("Invalid token.")
            return False
