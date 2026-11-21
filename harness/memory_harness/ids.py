import uuid6


def uuid7() -> str:
    'A new time-sortable UUIDv7 as a lowercase hex string with dashes.'
    return str(uuid6.uuid7())
