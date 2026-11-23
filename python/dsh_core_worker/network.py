"""Translate the launcher's dual IPv6 bypass spelling for Python HTTP clients."""

import ipaddress
import os


def configure_environment(environ=None):
    environ = os.environ if environ is None else environ
    for key in ("no_proxy", "NO_PROXY"):
        if key not in environ:
            continue
        entries = []
        for raw in environ[key].split(","):
            value = raw.strip()
            if value.startswith("[") and value.endswith("]"):
                try:
                    value = str(ipaddress.IPv6Address(value[1:-1]))
                except ValueError:
                    pass
            if value not in entries:
                entries.append(value)
        environ[key] = ",".join(entries)
