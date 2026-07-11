"""Shared provider exception hierarchy."""


class RateLimitError(Exception):
    """Raised when a provider quota or availability limit is exhausted."""


class ApiKeyError(Exception):
    """Raised when a provider API key is missing or invalid."""


class GrokApiKeyError(ApiKeyError):
    """Raised when a Grok API key is missing or invalid."""


class OpenRouterApiKeyError(ApiKeyError):
    """Raised when an OpenRouter API key is missing or invalid."""


class GroundingTransientError(ConnectionError):
    """Raised for retryable Gemini Google Search grounding failures."""
