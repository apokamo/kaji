"""Error classes for core modules."""


class IssueProviderError(Exception):
    """Base exception for IssueProvider operations.

    All IssueProvider implementations should raise this or its subclasses
    for API errors, network failures, and other operational issues.
    """

    pass


class IssueNotFoundError(IssueProviderError):
    """Issue does not exist.

    Raised when attempting to access an issue that doesn't exist
    or has been deleted.
    """

    pass


class IssueAuthenticationError(IssueProviderError):
    """Authentication failure.

    Raised when the issue provider cannot authenticate,
    e.g., gh CLI not logged in, invalid token, etc.
    """

    pass


class IssueRateLimitError(IssueProviderError):
    """API rate limit exceeded.

    Raised when the issue provider API rate limit is hit.
    Callers may want to implement backoff/retry logic.
    """

    pass
