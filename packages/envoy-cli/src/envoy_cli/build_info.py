"""Build identity defaults for editable source checkouts."""

from . import __version__

BUILD_CHANNEL = "source-checkout"
BUILD_REVISION = "working-tree"
BUILD_DIRTY = True
BUILD_VERSION = __version__
