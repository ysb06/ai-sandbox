"""Crawl orchestration helpers for ytcrawl."""

__all__ = ["crawl_youtube"]


def __getattr__(name: str):
    if name == "crawl_youtube":
        from ytcrawl.crawl.youtube import crawl_youtube

        return crawl_youtube
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
