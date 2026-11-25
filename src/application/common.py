"""Common service result envelope, additive for existing UI/report compatibility."""
from src.domain.results import summarize


def attach_summary(service, result, sources=()):
    result["_analysis"] = summarize(service, result, sources).to_dict()
    return result
