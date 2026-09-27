from job_alert.sources.ashby import AshbyAdapter
from job_alert.sources.base import ATSAdapter, BoardResult, FeedSource
from job_alert.sources.greenhouse import GreenhouseAdapter
from job_alert.sources.lever import LeverAdapter
from job_alert.sources.simplify import SimplifySource

ADAPTERS: dict[str, ATSAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
}

__all__ = ["ADAPTERS", "ATSAdapter", "BoardResult", "FeedSource", "SimplifySource"]
