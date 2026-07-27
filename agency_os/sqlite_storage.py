"""Local filesystem safety checks for security-sensitive SQLite databases."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class SQLiteStorageError(RuntimeError):
    """The configured SQLite path is not safe for authoritative state."""


@dataclass(frozen=True)
class SQLiteStorageIdentity:
    parent_device: int
    parent_inode: int
    database_device: int
    database_inode: int


def prepare_sqlite_storage(database_path: Path) -> SQLiteStorageIdentity:
    """Securely create an absent database and pin its filesystem identity."""

    parent_stat = _validate_parent(database_path.parent)
    try:
        os.lstat(database_path)
    except FileNotFoundError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(database_path, flags, 0o600)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SQLiteStorageError("could not create SQLite database securely") from exc
        else:
            os.close(descriptor)
    except OSError as exc:
        raise SQLiteStorageError("could not inspect SQLite database") from exc

    identity = validate_sqlite_storage(database_path)
    if (
        identity.parent_device != parent_stat.st_dev
        or identity.parent_inode != parent_stat.st_ino
    ):
        raise SQLiteStorageError("SQLite parent directory changed during setup")
    return identity


def validate_sqlite_storage(
    database_path: Path,
    expected_identity: SQLiteStorageIdentity | None = None,
) -> SQLiteStorageIdentity:
    """Require safe ownership/modes and an unchanged file and parent identity."""

    parent_stat = _validate_parent(database_path.parent)
    try:
        database_stat = os.lstat(database_path)
    except OSError as exc:
        raise SQLiteStorageError("could not inspect SQLite database") from exc
    if stat.S_ISLNK(database_stat.st_mode) or not stat.S_ISREG(database_stat.st_mode):
        raise SQLiteStorageError("SQLite database must be a non-symlink regular file")
    if database_stat.st_uid != os.geteuid():
        raise SQLiteStorageError("SQLite database must be owned by the service account")
    if stat.S_IMODE(database_stat.st_mode) != 0o600:
        raise SQLiteStorageError("SQLite database mode must be 0600")

    identity = SQLiteStorageIdentity(
        parent_device=parent_stat.st_dev,
        parent_inode=parent_stat.st_ino,
        database_device=database_stat.st_dev,
        database_inode=database_stat.st_ino,
    )
    if expected_identity is not None and identity != expected_identity:
        raise SQLiteStorageError("SQLite storage identity changed")
    return identity


def _validate_parent(parent_path: Path) -> os.stat_result:
    try:
        parent_stat = os.lstat(parent_path)
    except OSError as exc:
        raise SQLiteStorageError("could not inspect SQLite parent directory") from exc
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        raise SQLiteStorageError("SQLite parent must be a non-symlink directory")
    if parent_stat.st_uid != os.geteuid():
        raise SQLiteStorageError("SQLite parent must be owned by the service account")
    if stat.S_IMODE(parent_stat.st_mode) & 0o022:
        raise SQLiteStorageError(
            "SQLite parent must not be group- or other-writable"
        )
    return parent_stat
