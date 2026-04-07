from backend.data.news_fetcher import NewsFetcher


def test_score_article_uses_neutral_fallback_when_sentiment_pipeline_is_unavailable(monkeypatch):
    def _return_none(cls, timeout_seconds=None):
        return None

    monkeypatch.setattr(NewsFetcher, "preload_sentiment_pipeline", classmethod(lambda cls, wait=False: False))
    monkeypatch.setattr(NewsFetcher, "ensure_sentiment_pipeline", classmethod(_return_none))
    fetcher = NewsFetcher()

    label, signed_score, confidence = fetcher.score_article("Test headline", "Test body")

    assert label == "NEUTRAL"
    assert signed_score == 0.0
    assert confidence == 0.0
