from geo.collector.adapters.brightdata import BrightDataAdapter
from geo.collector.adapters.cloro import CloroAdapter
from geo.collector.adapters.scrapellm import ScrapeLLMAdapter

REGISTRY = {
    "cloro": CloroAdapter,
    "brightdata": BrightDataAdapter,
    "scrapellm": ScrapeLLMAdapter,
}


def get_adapter(name: str):
    return REGISTRY[name]()
