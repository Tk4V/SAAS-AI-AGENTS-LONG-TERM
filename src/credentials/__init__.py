"""Credential domain: storage, encryption, audit, resolution.

Sole owner of the ``credentials`` table. Other modules read and write
secrets exclusively through ``CredentialService`` and ``CredentialResolver``.
"""

from src.credentials.exceptions import (
    CredentialKindNotSupported,
    InvalidCredentialPayload,
)
from src.credentials.resolver import CredentialResolver, ResolvedCredential
from src.credentials.service import CredentialService

__all__ = [
    "CredentialKindNotSupported",
    "CredentialResolver",
    "CredentialService",
    "InvalidCredentialPayload",
    "ResolvedCredential",
]
