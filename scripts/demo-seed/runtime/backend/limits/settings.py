"""Bounded configuration for the durable queue, shared by every worker."""
import os


def configured(name, default, maximum=1000000000):
    value = int(os.environ.get(name, default))
    if not 1 <= value <= maximum:
        raise ValueError(name + ' must be a positive bounded integer')
    return value
