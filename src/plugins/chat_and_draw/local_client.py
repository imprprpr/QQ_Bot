"""Optional private deployment adapter; the default remains the API-key client."""
import importlib.util
import os
from pathlib import Path

from .config import ROOT

_factories = {}
_clients = {}


def local_client(*, vision=False):
    value = os.getenv("LLM_CLIENT_FACTORY", "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    # An explicit local file is trusted code configured by the operator.
    if path not in _factories:
        spec = importlib.util.spec_from_file_location("bot_private_client", path)
        if spec is None or spec.loader is None:
            raise ValueError("INVALID_LOCAL_CLIENT_FACTORY")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _factories[path] = module.create_client
    key = (path, vision)
    if key not in _clients:
        _clients[key] = _factories[path](vision=vision)
    return _clients[key]


async def close_local_clients():
    clients = list({id(client): client for client in _clients.values()}.values())
    _clients.clear()
    for client in clients:
        await client.close()
