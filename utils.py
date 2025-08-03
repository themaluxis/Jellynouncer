"""
JellyNotify Utilities Module

This module provides shared utility functions including logging setup and common helpers.
Consolidates logging initialization to prevent duplicate setup.
"""

import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path


def setup_logging(log_level: str = "INFO", log_dir: str = "/app/logs") -> logging.Logger:
    """
    Set up comprehensive logging with rotation and custom formatting.

    This function configures Python's logging system with:
    - Console output for immediate feedback
    - File output with automatic rotation to prevent disk space issues
    - Custom formatting that includes timestamps and component names
    - Structured format suitable for both human reading and log analysis

    Args:
        log_level: Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory to store log files

    Returns:
        Configured logger instance for the application

    Example:
        ```python
        logger = setup_logging("DEBUG", "/var/logs/jellynotify")
        logger.info("Application starting up")
        logger.error("Database connection failed", exc_info=True)
        ```

    Note:
        The function uses RotatingFileHandler to automatically manage log file
        sizes and keep old logs as backup files. This prevents logs from
        consuming unlimited disk space over time.
    """
    # Create logs directory if it doesn't exist
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    class BracketFormatter(logging.Formatter):
        """
        Custom log formatter that uses brackets for structured, readable output.

        This formatter creates log lines in the format:
        [timestamp] [user] [level] [component] message

        The structured format makes it easier to parse logs programmatically
        while remaining human-readable.
        """

        def format(self, record):
            # Get UTC timestamp for consistency across time zones
            timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

            # Get user context (could be extended for multi-user scenarios)
            user = getattr(record, 'user', 'system')

            # Format the complete log message
            return f"[{timestamp}] [{user}] [{record.levelname}] [{record.name}] {record.getMessage()}"

    # Create the main application logger
    logger = logging.getLogger("jellynotify")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Clear any existing handlers to prevent duplicate logs
    logger.handlers.clear()

    # Console handler for immediate feedback during development/debugging
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(BracketFormatter())
    logger.addHandler(console_handler)

    # Rotating file handler to prevent logs from consuming unlimited disk space
    # 10MB per file, keep 5 backup files = maximum 50MB total
    file_handler = logging.handlers.RotatingFileHandler(
        filename=Path(log_dir) / "jellynotify.log",
        maxBytes=10 * 1024 * 1024,  # 10MB per file
        backupCount=5,  # Keep 5 backup files (jellynotify.log.1, .2, etc.)
        encoding='utf-8',
        mode='a'  # Append mode - preserves logs across restarts
    )
    file_handler.setLevel(getattr(logging, log_level.upper()))
    file_handler.setFormatter(BracketFormatter())
    logger.addHandler(file_handler)

    # Disable uvicorn's access logger to avoid duplication with our custom logging
    logging.getLogger("uvicorn.access").disabled = True

    # Log the configuration for verification and debugging
    logger.info("=" * 60)
    logger.info("JellyNotify Logging Configuration")
    logger.info("=" * 60)
    logger.info(f"Log Level: {log_level.upper()}")
    logger.info(f"Log Directory: {log_dir}")
    logger.info(f"Main Log File: {Path(log_dir) / 'jellynotify.log'}")
    logger.info(f"Max Log Size: 10MB")
    logger.info(f"Backup Count: 5")
    logger.info(f"Total Handlers: {len(logger.handlers)}")
    for i, handler in enumerate(logger.handlers):
        logger.info(f"Handler {i + 1}: {type(handler).__name__} - Level: {logging.getLevelName(handler.level)}")
    logger.info("=" * 60)

    return logger


def get_logger(name: str = "jellynotify") -> logging.Logger:
    """
    Get an existing logger instance by name.

    This function retrieves a logger that should have been previously
    configured by setup_logging(). Used throughout the application
    to get consistent logging.

    Args:
        name: Logger name to retrieve

    Returns:
        Logger instance

    Example:
        ```python
        logger = get_logger("jellynotify.database")
        logger.info("Database operation completed")
        ```
    """
    return logging.getLogger(name)


def format_bytes(bytes_value: int) -> str:
    """
    Format byte count into human-readable string.

    Args:
        bytes_value: Number of bytes

    Returns:
        Formatted string with appropriate unit

    Example:
        ```python
        format_bytes(1024)       # "1.0 KB"
        format_bytes(1048576)    # "1.0 MB"
        format_bytes(1073741824) # "1.0 GB"
        ```
    """
    if bytes_value == 0:
        return "0 B"

    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    size = float(bytes_value)

    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    return f"{size:.1f} {units[unit_index]}"


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename for safe filesystem usage.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename safe for filesystem use

    Example:
        ```python
        sanitize_filename("Movie: The Sequel (2023)")  # "Movie_ The Sequel (2023)"
        sanitize_filename("TV Show/Episode 1")         # "TV Show_Episode 1"
        ```
    """
    # Replace problematic characters with underscores
    problematic_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    sanitized = filename

    for char in problematic_chars:
        sanitized = sanitized.replace(char, '_')

    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip(' .')

    # Ensure filename isn't empty
    if not sanitized:
        sanitized = "unnamed"

    return sanitized


def truncate_string(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate string to maximum length with suffix.

    Args:
        text: String to truncate
        max_length: Maximum allowed length including suffix
        suffix: Suffix to add when truncating

    Returns:
        Truncated string with suffix if needed

    Example:
        ```python
        truncate_string("Very long text here", 10)  # "Very lo..."
        truncate_string("Short", 10)                # "Short"
        ```
    """
    if len(text) <= max_length:
        return text

    if len(suffix) >= max_length:
        return text[:max_length]

    return text[:max_length - len(suffix)] + suffix


def safe_get_nested_value(data: dict, path: list, default=None):
    """
    Safely get a nested value from a dictionary using a path.

    Args:
        data: Dictionary to search
        path: List of keys representing the path
        default: Default value if path doesn't exist

    Returns:
        Value at path or default

    Example:
        ```python
        data = {"a": {"b": {"c": "value"}}}
        safe_get_nested_value(data, ["a", "b", "c"])  # "value"
        safe_get_nested_value(data, ["a", "x", "y"])  # None
        ```
    """
    current = data
    try:
        for key in path:
            current = current[key]
        return current
    except (KeyError, TypeError):
        return default