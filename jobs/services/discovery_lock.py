"""Serialize discovery execution; PostgreSQL session lock, process lock in tests."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import Lock

from django.db import connection


LOCK_KEY = 7548132901
_current = ContextVar('discovery_ownership', default=None)
_test_lock = Lock()


class DiscoveryBusy(RuntimeError):
    pass


class DiscoveryOwnershipLost(RuntimeError):
    pass


class Ownership:
    def __init__(self, raw_connection):
        self.raw_connection = raw_connection

    def check(self):
        if connection.vendor == 'postgresql' and (
                connection.connection is not self.raw_connection or self.raw_connection.closed):
            raise DiscoveryOwnershipLost('Discovery worker lost its database lock; execution stopped')


@contextmanager
def discovery_execution_lock():
    existing = _current.get()
    if existing is not None:
        existing.check()
        yield existing
        return
    postgres = connection.vendor == 'postgresql'
    if postgres:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_KEY])
            acquired = cursor.fetchone()[0]
    else:
        acquired = _test_lock.acquire(blocking=False)
    if not acquired:
        raise DiscoveryBusy('Another discovery process is already running')
    owner = Ownership(connection.connection)
    token = _current.set(owner)
    try:
        yield owner
    finally:
        _current.reset(token)
        if postgres:
            # Never unlock using a replacement connection after losing ownership.
            if connection.connection is owner.raw_connection and not owner.raw_connection.closed:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_KEY])
        else:
            _test_lock.release()


def exclusive_discovery(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with discovery_execution_lock() as owner:
            owner.check()
            return function(*args, **kwargs)
    return wrapped


def check_discovery_ownership():
    owner = _current.get()
    if owner is not None:
        owner.check()
