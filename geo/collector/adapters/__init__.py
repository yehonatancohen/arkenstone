from geo.collector.adapters.brightdata import BrightDataAdapter
from geo.collector.adapters.cloro import CloroAdapter

REGISTRY = {
    "cloro": CloroAdapter,
    "brightdata": BrightDataAdapter,
}


def get_adapter(name: str):
    return REGISTRY[name]()
