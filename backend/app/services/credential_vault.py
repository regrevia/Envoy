from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import CREDENTIAL_KEY_PATH, CREDENTIAL_VAULT_DIR


class CredentialVaultError(RuntimeError):
    pass


def _key() -> bytes:
    raw = os.getenv("ENVOY_CREDENTIAL_VAULT_KEY", "").strip()
    if raw:
        return raw.encode("ascii")

    path = Path(CREDENTIAL_KEY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return path.read_bytes().strip()

    value = Fernet.generate_key()
    path.write_bytes(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def _fernet() -> Fernet:
    try:
        return Fernet(_key())
    except Exception as exc:
        raise CredentialVaultError("Credential vault key is invalid") from exc


def store_cookie_bytes(raw: bytes) -> str:
    if not raw:
        raise CredentialVaultError("Cookie file is empty")
    if len(raw) > 5 * 1024 * 1024:
        raise CredentialVaultError("Cookie file is larger than 5 MiB")
    credential_id = uuid4().hex
    root = Path(CREDENTIAL_VAULT_DIR)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{credential_id}.enc"
    path.write_bytes(_fernet().encrypt(raw))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return credential_id


def delete_credential(credential_id: str) -> None:
    path = Path(CREDENTIAL_VAULT_DIR) / f"{credential_id}.enc"
    try:
        path.unlink()
    except FileNotFoundError:
        pass


@contextmanager
def materialize_cookie_file(credential_id: str) -> Iterator[str]:
    if not credential_id or Path(credential_id).name != credential_id:
        raise CredentialVaultError("Invalid credential id")
    encrypted_path = Path(CREDENTIAL_VAULT_DIR) / f"{credential_id}.enc"
    try:
        raw = _fernet().decrypt(encrypted_path.read_bytes())
    except FileNotFoundError as exc:
        raise CredentialVaultError(f"Credential not found: {credential_id}") from exc
    except (InvalidToken, OSError) as exc:
        raise CredentialVaultError("Unable to decrypt credential") from exc

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="envoy-cookies-",
            suffix=".txt",
            dir=str(Path(CREDENTIAL_VAULT_DIR)),
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(raw)
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        yield str(temp_path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
