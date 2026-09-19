"""Paths that work from source and from a frozen .exe."""

import os
import sys


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def project_dir():
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    if is_frozen():
        return getattr(sys, "_MEIPASS", project_dir())
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    if is_frozen():
        root = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "WhiteboardTool",
        )
        os.makedirs(root, exist_ok=True)
        return root
    return os.path.dirname(os.path.abspath(__file__))
