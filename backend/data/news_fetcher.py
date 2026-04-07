from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from newsapi import NewsApiClient
from requests.adapters import HTTPAdapter
from sqlalchemy import and_, false, or_, select
from transformers import pipeline
from urllib3.util.retry import Retry

from backend.config import get_settings
from backend.data.data_quality import sanitize_news_timestamp
from backend.db.postgres import NewsArticle, add_notification, get_config_value, session_scope, upsert_config_value
from backend.engine.event_risk_engine import extract_financial_catalyst
from backend.logging_utils import get_logger


settings = get_settings()
logger = get_logger(__name__)

COMPANY_SUFFIXES = {
    "LIMITED",
    "LTD",
    "LIMITED.",
    "LTD.",
    "INC",
    "INC.",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
    "PLC",
    "LLP",
}
GENERIC_QUERY_SYMBOLS = {"RELIANCE"}
AMBIGUOUS_SYMBOLS = {"TRENT", "TITAN", "BEL", "ACE"}
FINANCE_CONTEXT_TERMS = {
    "stock",
    "shares",
    "share",
    "nse",
    "bse",
    "earnings",
    "results",
    "profit",
    "revenue",
    "guidance",
    "brokerage",
    "target",
    "buyback",
    "dividend",
    "promoter",
    "ceo",
    "cfo",
    "board",
    "merger",
    "acquisition",
    "margin",
    "quarter",
    "q1",
    "q2",
    "q3",
    "q4",
    "order",
}
TRUSTED_SOURCE_KEYWORDS = {
    "moneycontrol",
    "economic times",
    "times of india",
    "businessline",
    "business standard",
    "mint",
    "livemint",
    "cnbctv18",
    "reuters",
    "bloomberg",
    "ndtv profit",
    "financial express",
    "zee business",
    "the hindu",
}
TRUSTED_URL_KEYWORDS = {
    "moneycontrol.com",
    "economictimes.indiatimes.com",
    "timesofindia.indiatimes.com",
    "thehindubusinessline.com",
    "business-standard.com",
    "livemint.com",
    "cnbctv18.com",
    "reuters.com",
    "bloomberg.com",
    "ndtvprofit.com",
    "financialexpress.com",
    "zeebiz.com",
    "thehindu.com",
}
BLOCKED_SOURCE_KEYWORDS = {
    "yahoo entertainment",
    "digitimes",
    "patentlyo",
    "ipwatchdog",
    "variety",
    "billboard",
    "espn",
    "sportstar",
}
FREE_NEWS_SEARCH_DOMAINS = (
    "moneycontrol.com",
    "economictimes.indiatimes.com",
    "business-standard.com",
    "livemint.com",
    "cnbctv18.com",
    "ndtvprofit.com",
    "financialexpress.com",
)
NON_ALERT_SCRAPER_STATES = {"OK", "NO_MATCH"}


def _truncate_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if len(text) > limit else text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_news_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _normalized_token_hits(text: str, aliases: list[str]) -> float:
    score = 0.0
    for alias in aliases:
        if not alias:
            continue
        if alias in text:
            if len(alias.split()) >= 2 or len(alias) >= 10:
                score = max(score, 0.9)
            elif len(alias) >= 6:
                score = max(score, 0.55)
            else:
                score = max(score, 0.30)
    return score


def _dedupe_key(headline: str | None, body_snippet: str | None, url: str | None) -> str:
    normalized_url = (url or "").strip().lower()
    if normalized_url:
        return normalized_url
    headline_key = _normalize_news_text(headline)
    body_key = _normalize_news_text(body_snippet)[:120]
    return f"{headline_key}|{body_key}"


def _published_sort_value(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).timestamp()
    return value.timestamp()


def _parse_timestamp_text(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    normalized = re.sub(r"\s+", " ", text)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(normalized)
        except (TypeError, ValueError, IndexError):
            parsed = None
    if parsed is None:
        lowered = normalized.lower()
        relative_match = re.search(r"(\d+)\s*(minute|min|hour|hr|day)\w*\s+ago", lowered)
        if relative_match:
            quantity = int(relative_match.group(1))
            unit = relative_match.group(2)
            delta = timedelta(minutes=quantity) if unit.startswith("min") else timedelta(hours=quantity) if unit.startswith(("hour", "hr")) else timedelta(days=quantity)
            parsed = _utc_now() - delta
        elif "yesterday" in lowered:
            parsed = _utc_now() - timedelta(days=1)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _company_aliases(company_name: str | None) -> list[str]:
    normalized = _normalize_news_text(company_name)
    if not normalized:
        return []
    tokens = [token for token in normalized.split() if token.upper() not in COMPANY_SUFFIXES]
    aliases = []
    if tokens:
        aliases.append(" ".join(tokens))
        if len(tokens) >= 2:
            aliases.append(" ".join(tokens[:2]))
    aliases.append(normalized)
    deduped: list[str] = []
    for alias in aliases:
        if alias and alias not in deduped:
            deduped.append(alias)
    return deduped


def _search_phrases(company_name: str | None, symbol: str) -> list[str]:
    aliases = _company_aliases(company_name)
    phrases: list[str] = []
    if company_name:
        cleaned_tokens = [
            token
            for token in re.split(r"\s+", company_name)
            if token and token.upper().rstrip(".") not in COMPANY_SUFFIXES
        ]
        cleaned_company_name = " ".join(cleaned_tokens).strip()
        if cleaned_company_name:
            phrases.append(cleaned_company_name)
    for alias in aliases:
        candidate = alias.strip()
        if candidate and candidate not in phrases:
            phrases.append(candidate)
    if symbol.upper() not in GENERIC_QUERY_SYMBOLS and symbol.upper() not in AMBIGUOUS_SYMBOLS:
        phrases.append(symbol.strip())
    deduped: list[str] = []
    for phrase in phrases:
        normalized = re.sub(r"\s+", " ", phrase).strip()
        if normalized and normalized.lower() not in {item.lower() for item in deduped}:
            deduped.append(normalized)
    return deduped[:3]


def article_source_is_market_relevant(source: str | None, url: str | None) -> bool:
    source_text = (source or "").lower().strip()
    url_text = (url or "").lower().strip()
    if not source_text and not url_text:
        return False
    if any(keyword in source_text for keyword in BLOCKED_SOURCE_KEYWORDS) or any(keyword in url_text for keyword in BLOCKED_SOURCE_KEYWORDS):
        return False
    return any(keyword in source_text for keyword in TRUSTED_SOURCE_KEYWORDS) or any(
        keyword in url_text for keyword in TRUSTED_URL_KEYWORDS
    )


def article_relevance_score(headline: str | None, body_snippet: str | None, *, symbol: str, company_name: str | None) -> float:
    headline_text = _normalize_news_text(headline)
    body_text = _normalize_news_text(body_snippet)
    text = " ".join(part for part in [headline_text, body_text] if part).strip()
    if not text:
        return 0.0

    aliases = _company_aliases(company_name)
    alias_score = max(_normalized_token_hits(text, aliases), _normalized_token_hits(headline_text, aliases) + 0.1)

    symbol_word = symbol.lower().strip()
    exact_symbol_match = bool(symbol_word and re.search(rf"\b{re.escape(symbol_word)}\b", text))
    symbol_in_headline = bool(symbol_word and re.search(rf"\b{re.escape(symbol_word)}\b", headline_text))
    finance_context_hits = sum(1 for term in FINANCE_CONTEXT_TERMS if re.search(rf"\b{re.escape(term)}\b", text))

    score = alias_score
    if exact_symbol_match:
        score += 0.28 if symbol.upper() not in GENERIC_QUERY_SYMBOLS and symbol.upper() not in AMBIGUOUS_SYMBOLS else 0.16
    if symbol_in_headline:
        score += 0.08
    score += min(0.35, finance_context_hits * 0.06)

    if symbol.upper() in GENERIC_QUERY_SYMBOLS or symbol.upper() in AMBIGUOUS_SYMBOLS:
        if alias_score < 0.75:
            score -= 0.30
        if finance_context_hits == 0:
            score -= 0.15
    elif exact_symbol_match and alias_score < 0.35 and finance_context_hits == 0:
        score -= 0.20

    return max(0.0, min(1.5, score))


def article_is_relevant_to_symbol(headline: str | None, body_snippet: str | None, *, symbol: str, company_name: str | None) -> bool:
    return article_relevance_score(headline, body_snippet, symbol=symbol, company_name=company_name) >= settings.news_relevance_threshold


class NewsFetcher:
    _pipeline_lock = Lock()
    _pipeline_ready = Event()
    _pipeline_loading_started = False
    _shared_sentiment_pipeline = None
    _shared_sentiment_error: str | None = None
    _logged_sentiment_fallback = False

    def __init__(self) -> None:
        self.news_api = NewsApiClient(api_key=settings.news_api_key) if settings.news_api_key else None
        self.http = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
        self.http.mount("https://", adapter)
        self.http.mount("http://", adapter)
        self.http.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    @staticmethod
    def _upsert_scraper_health(source: str, *, status: str, details: str) -> None:
        today_key = _utc_now().date().isoformat()
        with session_scope() as session:
            state = get_config_value(session, "news_scraper_health", {"sources": {}}) or {"sources": {}}
            sources = dict(state.get("sources") or {})
            source_state = dict(sources.get(source) or {})
            last_alert_date = source_state.get("lastAlertDate")
            source_state.update(
                {
                    "status": status,
                    "details": details,
                    "updatedAt": _utc_now().isoformat(),
                }
            )
            if status not in NON_ALERT_SCRAPER_STATES and last_alert_date != today_key:
                add_notification(
                    session,
                    notification_type="NEWS_SCRAPER",
                    title=f"{source} scraper warning",
                    body=details,
                    color="orange",
                )
                source_state["lastAlertDate"] = today_key
            elif status in NON_ALERT_SCRAPER_STATES:
                source_state["lastAlertDate"] = last_alert_date
            sources[source] = source_state
            state["sources"] = sources
            upsert_config_value(session, "news_scraper_health", state)

    @staticmethod
    def _story_nodes(soup: BeautifulSoup, selectors: tuple[str, ...]) -> list[Any]:
        for selector in selectors:
            nodes = soup.select(selector)
            if nodes:
                return nodes
        return []

    @staticmethod
    def _extract_node_published_at(node: Any) -> datetime | None:
        selectors = (
            "time[datetime]",
            "time",
            "[data-time]",
            "[data-date]",
            "span[class*='time']",
            "span[class*='date']",
            "p[class*='time']",
            "p[class*='date']",
        )
        for selector in selectors:
            element = node.select_one(selector)
            if element is None:
                continue
            for candidate in (
                element.get("datetime"),
                element.get("content"),
                element.get("data-time"),
                element.get("data-date"),
                element.get_text(" ", strip=True),
            ):
                parsed = _parse_timestamp_text(candidate)
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
    def _newsapi_usage_key() -> str:
        return _utc_now().date().isoformat()

    def _load_newsapi_state(self) -> dict[str, Any]:
        today = self._newsapi_usage_key()
        with session_scope() as session:
            state = get_config_value(
                session,
                "newsapi_usage_state",
                {"date": today, "requestCount": 0, "rateLimited": False},
            ) or {"date": today, "requestCount": 0, "rateLimited": False}
        if state.get("date") != today:
            state = {"date": today, "requestCount": 0, "rateLimited": False}
        return state

    def _store_newsapi_state(self, state: dict[str, Any]) -> None:
        with session_scope() as session:
            upsert_config_value(session, "newsapi_usage_state", state)

    def _newsapi_request_allowed(self) -> bool:
        state = self._load_newsapi_state()
        return (not bool(state.get("rateLimited"))) and int(state.get("requestCount") or 0) < settings.news_api_daily_soft_limit

    def _record_newsapi_request(self, *, rate_limited: bool = False) -> None:
        state = self._load_newsapi_state()
        state["requestCount"] = int(state.get("requestCount") or 0) + 1
        if rate_limited:
            state["rateLimited"] = True
        self._store_newsapi_state(state)

    @property
    def sentiment_pipeline(self):
        return self.ensure_sentiment_pipeline(timeout_seconds=settings.finbert_preload_timeout_seconds)

    @classmethod
    def _load_sentiment_pipeline(cls) -> None:
        try:
            model = pipeline("text-classification", model="ProsusAI/finbert")
            with cls._pipeline_lock:
                cls._shared_sentiment_pipeline = model
                cls._shared_sentiment_error = None
            logger.info("FinBERT model loaded")
        except Exception as exc:
            with cls._pipeline_lock:
                cls._shared_sentiment_pipeline = None
                cls._shared_sentiment_error = str(exc)
                cls._pipeline_loading_started = False
            logger.warning("FinBERT preload failed: %s", exc)
        finally:
            cls._pipeline_ready.set()

    @classmethod
    def preload_sentiment_pipeline(cls, *, wait: bool = False) -> bool:
        if cls._shared_sentiment_pipeline is not None:
            return True
        with cls._pipeline_lock:
            if cls._shared_sentiment_pipeline is not None:
                return True
            if not cls._pipeline_loading_started:
                cls._pipeline_loading_started = True
                cls._pipeline_ready.clear()
                Thread(target=cls._load_sentiment_pipeline, daemon=True, name="finbert-preload").start()
        if wait:
            cls._pipeline_ready.wait(timeout=settings.finbert_preload_timeout_seconds)
        return cls._shared_sentiment_pipeline is not None

    @classmethod
    def ensure_sentiment_pipeline(cls, *, timeout_seconds: float | None = None):
        if cls._shared_sentiment_pipeline is not None:
            return cls._shared_sentiment_pipeline
        cls.preload_sentiment_pipeline(wait=False)
        if timeout_seconds and timeout_seconds > 0:
            cls._pipeline_ready.wait(timeout=timeout_seconds)
        return cls._shared_sentiment_pipeline

    def fetch_news_api_articles(self, company_name: str, symbol: str, from_date: datetime, to_date: datetime) -> list[dict[str, Any]]:
        if self.news_api is None or not self._newsapi_request_allowed():
            return []
        query_terms = [f'"{term}"' for term in _search_phrases(company_name, symbol)]
        query = " OR ".join(query_terms) if query_terms else f'"{symbol}"'
        try:
            response = self.news_api.get_everything(
                q=query,
                from_param=from_date.strftime("%Y-%m-%d"),
                to=to_date.strftime("%Y-%m-%d"),
                language="en",
                sort_by="publishedAt",
                page_size=100,
            )
            self._record_newsapi_request()
        except Exception as exc:
            message = str(exc)
            rate_limited = "429" in message or "rate limit" in message.lower()
            self._record_newsapi_request(rate_limited=rate_limited)
            self._upsert_scraper_health(
                "NewsAPI",
                status="RATE_LIMIT" if rate_limited else "HTTP_ERROR",
                details=message or "NewsAPI request failed.",
            )
            return []
        articles = response.get("articles", [])
        self._upsert_scraper_health("NewsAPI", status="OK", details=f"Fetched {len(articles)} articles.")
        return [
            {
                "source": article.get("source", {}).get("name", "NewsAPI"),
                "headline": article.get("title"),
                "body_snippet": article.get("description") or article.get("content"),
                "url": article.get("url"),
                "published_at": article.get("publishedAt"),
            }
            for article in articles
        ]

    def fetch_google_news_rss_articles(self, company_name: str, symbol: str) -> list[dict[str, Any]]:
        phrases = [f'"{term}"' for term in _search_phrases(company_name, symbol)]
        if not phrases:
            return []
        site_clause = " OR ".join(f"site:{domain}" for domain in FREE_NEWS_SEARCH_DOMAINS)
        finance_clause = "(stock OR shares OR earnings OR results OR profit OR revenue OR margin)"
        query = f"({' OR '.join(phrases)}) {finance_clause} ({site_clause})"
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
        response = self.http.get(url, timeout=10)
        if response.status_code != 200:
            self._upsert_scraper_health("Google News RSS", status="HTTP_ERROR", details=f"HTTP {response.status_code} for {url}")
            return []
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            self._upsert_scraper_health("Google News RSS", status="PARSE_ERROR", details=str(exc))
            return []
        articles: list[dict[str, Any]] = []
        for item in root.findall(".//item")[: settings.news_scraper_result_limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = item.findtext("description") or ""
            source = (item.findtext("source") or "Google News RSS").strip()
            body_snippet = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
            published_at = _parse_timestamp_text(item.findtext("pubDate"))
            if not title or not link:
                continue
            articles.append(
                {
                    "source": source,
                    "headline": title,
                    "body_snippet": body_snippet,
                    "url": link,
                    "published_at": published_at.isoformat() if published_at else None,
                }
            )
        if not articles:
            self._upsert_scraper_health(
                "Google News RSS",
                status="NO_MATCH",
                details=f"No RSS items matched for {url}.",
            )
        else:
            self._upsert_scraper_health("Google News RSS", status="OK", details=f"Parsed {len(articles)} articles.")
        return articles

    def scrape_moneycontrol(self, company_name: str, symbol: str) -> list[dict[str, Any]]:
        primary_phrase = next((phrase for phrase in _search_phrases(company_name, symbol) if " " in phrase), company_name or symbol)
        query = quote_plus(primary_phrase)
        url = f"https://www.moneycontrol.com/news/tags/{query}.html"
        response = self.http.get(url, timeout=10)
        if response.status_code != 200:
            self._upsert_scraper_health("MoneyControl", status="HTTP_ERROR", details=f"HTTP {response.status_code} for {url}")
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        articles: list[dict[str, Any]] = []
        selectors = (
            "li.clearfix",
            "ul.clearfix li",
            "div.tags_lastest_news ul li",
            "div.common_list li",
            "div#news li",
        )
        nodes = self._story_nodes(soup, selectors)
        for item in nodes[: settings.news_scraper_result_limit]:
            headline = item.select_one("h2, h3, a")
            link = item.select_one("a")
            summary = item.select_one("p")
            if not headline or not link:
                continue
            published_at = self._extract_node_published_at(item)
            articles.append(
                {
                    "source": "MoneyControl",
                    "headline": headline.get_text(" ", strip=True),
                    "body_snippet": summary.get_text(" ", strip=True) if summary else "",
                    "url": link.get("href"),
                    "published_at": published_at.isoformat() if published_at else None,
                }
            )
        if not articles:
            self._upsert_scraper_health(
                "MoneyControl",
                status="SELECTOR_EMPTY",
                details=f"No article cards matched expected selectors for {url}. Layout may have changed.",
            )
        else:
            self._upsert_scraper_health("MoneyControl", status="OK", details=f"Parsed {len(articles)} articles.")
        return articles

    def scrape_economic_times(self, company_name: str, symbol: str) -> list[dict[str, Any]]:
        primary_phrase = next((phrase for phrase in _search_phrases(company_name, symbol) if " " in phrase), company_name or symbol)
        query = quote_plus(primary_phrase)
        url = f"https://economictimes.indiatimes.com/topic/{query}"
        response = self.http.get(url, timeout=10)
        if response.status_code != 200:
            self._upsert_scraper_health("Economic Times", status="HTTP_ERROR", details=f"HTTP {response.status_code} for {url}")
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        articles: list[dict[str, Any]] = []
        selectors = (
            "div.eachStory",
            "div.topicstry story, div.topicstry",
            "div.topic_listing li",
            "article",
        )
        nodes = self._story_nodes(soup, selectors)
        for item in nodes[: settings.news_scraper_result_limit]:
            headline = item.select_one("h3, h2, a")
            link = item.select_one("a")
            summary = item.select_one("p")
            if not headline or not link:
                continue
            href = link.get("href", "")
            if href and not href.startswith("http"):
                href = "https://economictimes.indiatimes.com" + href
            published_at = self._extract_node_published_at(item)
            articles.append(
                {
                    "source": "Economic Times",
                    "headline": headline.get_text(" ", strip=True),
                    "body_snippet": summary.get_text(" ", strip=True) if summary else "",
                    "url": href,
                    "published_at": published_at.isoformat() if published_at else None,
                }
            )
        if not articles:
            self._upsert_scraper_health(
                "Economic Times",
                status="SELECTOR_EMPTY",
                details=f"No article cards matched expected selectors for {url}. Layout may have changed.",
            )
        else:
            self._upsert_scraper_health("Economic Times", status="OK", details=f"Parsed {len(articles)} articles.")
        return articles

    def score_article(self, headline: str, body_snippet: str) -> tuple[str, float, float]:
        text = " ".join(part for part in [headline, body_snippet] if part).strip()
        sentiment_pipeline = self.sentiment_pipeline
        if sentiment_pipeline is None:
            if not self.__class__._logged_sentiment_fallback:
                logger.info("FinBERT not ready yet; using neutral sentiment fallback for article scoring")
                self.__class__._logged_sentiment_fallback = True
            return "NEUTRAL", 0.0, 0.0
        result = sentiment_pipeline(text[:512])[0]
        label = result["label"].upper()
        confidence = float(result["score"])
        signed_score = confidence if label == "POSITIVE" else -confidence if label == "NEGATIVE" else 0.0
        return label, signed_score, confidence

    def fetch_and_store_symbol_news(
        self,
        *,
        symbol: str,
        company_name: str,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> int:
        to_date = to_date or _utc_now()
        from_date = from_date or (to_date - timedelta(days=3650))
        collected: list[dict[str, Any]] = []
        free_articles: list[dict[str, Any]] = []
        free_articles.extend(self.fetch_google_news_rss_articles(company_name, symbol))
        free_articles.extend(self.scrape_moneycontrol(company_name, symbol))
        free_articles.extend(self.scrape_economic_times(company_name, symbol))
        collected.extend(free_articles)
        if len(free_articles) < max(6, settings.news_scraper_result_limit // 2):
            collected.extend(self.fetch_news_api_articles(company_name, symbol, from_date, to_date))

        count = 0
        batch_seen: set[str] = set()
        with session_scope() as session:
            for item in collected:
                headline = _truncate_text(item.get("headline") or "", 500) or ""
                body_snippet = item.get("body_snippet") or ""
                source = _truncate_text(item.get("source"), 100)
                url = _truncate_text(item.get("url"), 1000)
                if not article_source_is_market_relevant(source, url):
                    continue
                relevance_score = article_relevance_score(headline, body_snippet, symbol=symbol, company_name=company_name)
                if relevance_score < settings.news_relevance_threshold:
                    continue
                dedupe_key = _dedupe_key(headline, body_snippet, url)
                if dedupe_key in batch_seen:
                    continue
                existing = session.scalar(
                    select(NewsArticle).where(and_(NewsArticle.symbol == symbol)).where(
                        or_(
                            NewsArticle.headline == headline,
                            NewsArticle.url == url if url else false(),
                        )
                    )
                )
                if existing:
                    batch_seen.add(dedupe_key)
                    continue
                label, signed_score, confidence = self.score_article(headline, body_snippet)
                published_at = item.get("published_at")
                published_at = sanitize_news_timestamp(published_at)
                session.add(
                    NewsArticle(
                        symbol=symbol,
                        company_name=company_name,
                        source=source,
                        headline=headline,
                        body_snippet=body_snippet,
                        url=url,
                        published_at=published_at,
                        sentiment_label=label,
                        sentiment_score=signed_score,
                        sentiment_confidence=confidence,
                    )
                )
                batch_seen.add(dedupe_key)
                count += 1
        return count

    def get_sentiment_for_date(self, symbol: str, as_of: datetime) -> float:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=settings.tzinfo)
        with session_scope() as session:
            rows = session.scalars(
                select(NewsArticle)
                .where(
                    and_(
                        NewsArticle.symbol == symbol,
                        NewsArticle.published_at <= as_of,
                    )
                )
                .order_by(NewsArticle.published_at.desc())
                .limit(40)
            ).all()
        relevant_rows = [
            article
            for article in rows
            if article_source_is_market_relevant(article.source, article.url)
            if article_relevance_score(
                article.headline,
                article.body_snippet,
                symbol=symbol,
                company_name=article.company_name,
            ) >= settings.news_relevance_threshold
        ]
        relevant_rows.sort(
            key=lambda article: (
                article_relevance_score(
                    article.headline,
                    article.body_snippet,
                    symbol=symbol,
                    company_name=article.company_name,
                ),
                _published_sort_value(article.published_at),
            ),
            reverse=True,
        )
        relevant_rows = relevant_rows[:10]
        if not relevant_rows:
            return 0.0
        weighted_score = 0.0
        total_weight = 0.0
        for article in relevant_rows:
            relevance = article_relevance_score(
                article.headline,
                article.body_snippet,
                symbol=symbol,
                company_name=article.company_name,
            )
            published_at = article.published_at or as_of
            age_hours = max((as_of - published_at).total_seconds() / 3600.0, 0.0)
            recency_weight = 1.25 if age_hours <= 6 else 1.1 if age_hours <= 24 else 0.9
            catalyst = extract_financial_catalyst(f"{article.headline or ''} {article.body_snippet or ''}")
            weight = max(0.1, 0.7 + relevance + recency_weight + catalyst.score)
            weighted_score += float(article.sentiment_score or 0.0) * weight
            total_weight += weight
        return max(-1.0, min(1.0, weighted_score / max(total_weight, 0.01)))

    def recent_intraday_catalyst_symbols(self, *, as_of: datetime, limit: int | None = None) -> list[str]:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=settings.tzinfo)
        limit = limit or settings.news_intraday_catalyst_limit
        lookback = timedelta(hours=settings.news_intraday_catalyst_lookback_hours)
        with session_scope() as session:
            rows = session.scalars(
                select(NewsArticle)
                .where(
                    and_(
                        NewsArticle.published_at.is_not(None),
                        NewsArticle.published_at >= as_of - lookback,
                        NewsArticle.published_at <= as_of,
                    )
                )
                .order_by(NewsArticle.published_at.desc())
                .limit(400)
            ).all()

        ranked: dict[str, float] = {}
        for article in rows:
            if not article.symbol or not article_source_is_market_relevant(article.source, article.url):
                continue
            catalyst = extract_financial_catalyst(f"{article.headline or ''} {article.body_snippet or ''}")
            moderate_positive_results = (
                catalyst.results_context
                and catalyst.score >= 0.30
                and float(article.sentiment_score or 0.0) >= 0.75
            )
            if not catalyst.is_positive and not moderate_positive_results:
                continue
            relevance = article_relevance_score(
                article.headline,
                article.body_snippet,
                symbol=article.symbol,
                company_name=article.company_name,
            )
            age_hours = max((as_of - (article.published_at or as_of)).total_seconds() / 3600.0, 0.0)
            recency = max(0.35, 1.0 - (age_hours / max(settings.news_intraday_catalyst_lookback_hours, 1)))
            score = (catalyst.score * 2.5) + (max(0.0, float(article.sentiment_score or 0.0)) * 1.5) + relevance + recency
            if moderate_positive_results:
                score += 0.4
            ranked[article.symbol] = max(ranked.get(article.symbol, 0.0), score)

        return [symbol for symbol, _score in sorted(ranked.items(), key=lambda item: item[1], reverse=True)[:limit]]
