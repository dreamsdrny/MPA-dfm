"""Normalization shared by evidence matching and template adapters."""
import re
import unicodedata


def normalize(text):
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).replace("\u3000", " ").lower()
    return re.sub(r"\s+", "", value)
