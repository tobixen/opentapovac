"""OpenTapoVac: local control of a TP-Link Tapo robot vacuum."""

try:
    from ._version import __version__
except ImportError:
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("opentapovac")
    except PackageNotFoundError:
        __version__ = "0+unknown"
