from bumblebee.sources.ashby import AshbyAdapter
from bumblebee.sources.base import ATSAdapter, BoardResult, FeedSource
from bumblebee.sources.greenhouse import GreenhouseAdapter
from bumblebee.sources.lever import LeverAdapter
from bumblebee.sources.simplify import SimplifySource

ADAPTERS: dict[str, ATSAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
}

__all__ = ["ADAPTERS", "ATSAdapter", "BoardResult", "FeedSource", "SimplifySource"]
