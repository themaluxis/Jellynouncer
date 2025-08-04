#!/usr/bin/env python3
"""
Jellynouncer Utilities Module

This module provides shared utility functions including logging setup and common helpers
used throughout the Jellynouncer application. It consolidates cross-cutting concerns
like logging configuration to prevent code duplication and ensure consistency.

The primary purpose is to provide a centralized location for utility functions that
don't belong to any specific service component but are needed across the application.
This promotes code reuse and maintains consistent behavior for common operations.

Functions:
    setup_logging: Configure logging with rotation and custom formatting
    get_logger: Retrieve existing logger instances by name
    format_bytes: Convert byte counts to human-readable format
    sanitize_filename: Clean filenames for safe filesystem usage

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import logging
import logging.handlers
import re
from datetime import datetime, timezone
from pathlib import Path


def setup_logging(log_level: str = "INFO", log_dir: str = "/app/logs") -> logging.Logger:
    """
    Set up logging with rotation and custom formatting.

    This function configures Python's logging system for production use with both
    console and file output. It's designed to provide detailed logging information
    while managing disk space usage through automatic log rotation.

    **Understanding Python Logging for Beginners:**

    Python's logging system allows applications to record events, errors, and
    diagnostic information. Key concepts:

    - **Loggers**: Named loggers that generate log messages
    - **Handlers**: Direct log messages to destinations (console, files, etc.)
    - **Formatters**: Control the format of log messages
    - **Levels**: Control message severity (DEBUG < INFO < WARNING < ERROR < CRITICAL)

    **Log Rotation Explained:**
    Without rotation, log files grow indefinitely and can fill up disk space.
    Log rotation automatically manages this by:
    - Creating new log files when current ones reach size limits
    - Keeping a fixed number of backup files
    - Automatically deleting the oldest files when limits are exceeded

    **Custom Formatting:**
    This function uses a structured log format with brackets for easy parsing:
    `[timestamp] [user] [level] [component] message`

    This format is both human-readable and machine-parseable for log analysis tools.

    **Logging Configuration:**
    - Console Handler: Shows messages at specified level for immediate feedback
    - File Handler: Stores all messages with automatic rotation
    - Custom Formatter: Structured format with UTC timestamps
    - Rotation: 10MB per file, 5 backup files (50MB total maximum)

    Args:
        log_level (str): Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            Both console and file handlers will use this level.
        log_dir (str): Directory path where log files will be stored.
            Created automatically if it doesn't exist.

    Returns:
        logging.Logger: Configured logger instance ready for use throughout the application

    Raises:
        ValueError: If log_level is not a valid Python logging level
        PermissionError: If log directory cannot be created or accessed

    Example:
        ```python
        # Basic setup for production
        logger = setup_logging("INFO", "/var/logs/jellynouncer")
        logger.info("Application starting up")
        logger.error("Database connection failed", exc_info=True)

        # Debug setup for development
        debug_logger = setup_logging("DEBUG", "./logs")
        debug_logger.debug("Detailed debugging information")
        debug_logger.warning("This is a warning message")

        # The logger will create structured output like:
        # [2025-01-15 10:30:45 UTC] [system] [INFO] [jellynouncer] Application starting up
        # [2025-01-15 10:30:46 UTC] [system] [ERROR] [jellynouncer] Database connection failed
        ```

    Note:
        This function should only be called once during application startup.
        Multiple calls will create duplicate log handlers, resulting in
        duplicate log messages. Use get_logger() to retrieve the configured
        logger in other parts of the application.

        The function uses RotatingFileHandler to automatically manage log file
        sizes and keep old logs as backup files. This prevents logs from
        consuming unlimited disk space over time.
    """
    # Validate log level first
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    log_level_upper = log_level.upper()
    if log_level_upper not in valid_levels:
        raise ValueError(f"Invalid log level '{log_level}'. Must be one of: {valid_levels}")

    # Convert string to logging constant
    numeric_level = getattr(logging, log_level_upper)

    # Create logs directory if it doesn't exist with error handling
    log_path = Path(log_dir)
    try:
        log_path.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise PermissionError(f"Cannot create log directory '{log_dir}': {e}")

    class BracketFormatter(logging.Formatter):
        """
        Custom log formatter that uses brackets for structured, readable output.

        This nested class creates a custom formatter that generates consistent,
        structured log messages. The bracket format makes it easy to parse logs
        programmatically while remaining human-readable.
        """

        def format(self, record: logging.LogRecord) -> str:
            """
            Format log record with structured bracket format.

            This method is called automatically by the logging system for each
            log message. It extracts information from the LogRecord and formats
            it according to our structured format.

            Args:
                record (LogRecord): Log record containing message and metadata

            Returns:
                str: Formatted log message ready for output

            Example:
                Input LogRecord with message "Database connected"
                Output: "[2025-01-15 10:30:45 UTC] [system] [INFO] [jellynouncer.db] Database connected"
            """
            # Get UTC timestamp for consistency across time zones and deployments
            timestamp = datetime.fromtimestamp(
                record.created,
                tz=timezone.utc
            ).strftime('%Y-%m-%d %H:%M:%S UTC')

            # Get user context (extensible for multi-user scenarios)
            # This allows tracking which user or process generated the log message
            user = getattr(record, 'user', 'system')

            # Format the complete log message with structured brackets
            return f"[{timestamp}] [{user}] [{record.levelname}] [{record.name}] {record.getMessage()}"

    # Create the main application logger with the specified name
    logger = logging.getLogger("jellynouncer")
    logger.setLevel(numeric_level)

    # Clear any existing handlers to prevent duplicate logs during testing/development
    # This is important if setup_logging() is called multiple times
    logger.handlers.clear()

    # Console handler for immediate feedback during development and debugging
    # Shows messages on the terminal/console for real-time monitoring
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)  # Use specified log level instead of hardcoded INFO
    console_handler.setFormatter(BracketFormatter())
    logger.addHandler(console_handler)

    # Rotating file handler to prevent logs from consuming unlimited disk space
    # This is crucial for production deployments that run continuously
    log_file_path = log_path / "jellynouncer.log"
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file_path,
            maxBytes=10 * 1024 * 1024,  # 10MB per file (reasonable size for analysis)
            backupCount=5,  # Keep 5 backup files (jellynouncer.log.1, .2, etc.)
            encoding='utf-8',  # Ensure proper encoding for international characters
            mode='a'  # Append mode - preserves logs across service restarts
        )
        file_handler.setLevel(numeric_level)  # Use specified log level for files
        file_handler.setFormatter(BracketFormatter())
        logger.addHandler(file_handler)
    except PermissionError as e:
        # If we can't create file handler, log to console only
        logger.error(f"Cannot create log file '{log_file_path}': {e}")
        logger.warning("Continuing with console logging only")

    # Disable uvicorn's access logger to avoid duplication with our custom logging
    # Uvicorn is the ASGI server that runs FastAPI applications
    logging.getLogger("uvicorn.access").disabled = True

    # Log the configuration for verification and debugging
    # This helps administrators verify logging is set up correctly
    logger.info("=" * 60)
    logger.info("Jellynouncer Logging Configuration")
    logger.info("=" * 60)
    logger.info(f"Log Level: {log_level_upper}")
    logger.info(f"Log Directory: {log_dir}")
    logger.info(f"Main Log File: {log_file_path}")
    logger.info(f"Max Log Size: 10MB per file")
    logger.info(f"Backup Count: 5 files")
    logger.info(f"Total Storage: 50MB maximum")
    logger.info(f"Total Handlers: {len(logger.handlers)}")

    # List each handler for diagnostic purposes
    for i, handler in enumerate(logger.handlers):
        logger.info(f"Handler {i + 1}: {type(handler).__name__} - Level: {logging.getLevelName(handler.level)}")

    # Test logging at different levels to verify configuration
    logger.debug("DEBUG level logging is working - visible when log level is DEBUG")
    logger.info("INFO level logging is working")

    logger.info("=" * 60)

    return logger


def get_logger(name: str = "jellynouncer") -> logging.Logger:
    """
    Get an existing logger instance by name.

    This function retrieves a logger that should have been previously configured
    by setup_logging(). It's used throughout the application to get consistent
    logging without reconfiguring the logging system.

    **Why Use This Function:**
    - Avoids reconfiguring logging multiple times
    - Ensures consistent logger names across the application
    - Provides a central point to modify logger retrieval if needed
    - Makes it easy to create component-specific loggers

    **Logger Naming Convention:**
    Logger names use a hierarchical structure with dots:
    - "jellynouncer" - Main application logger
    - "jellynouncer.database" - Database operations
    - "jellynouncer.discord" - Discord notifications
    - "jellynouncer.jellyfin" - Jellyfin API operations

    This hierarchy allows fine-grained control over logging levels for different
    components if needed in the future.

    Args:
        name (str): Logger name to retrieve. Defaults to main application logger.

    Returns:
        logging.Logger: Logger instance (may be newly created if name doesn't exist)

    Example:
        ```python
        # Get main application logger
        logger = get_logger()
        logger.info("Main application event")

        # Get component-specific logger
        db_logger = get_logger("jellynouncer.database")
        db_logger.debug("Database query executed")

        # Get Discord service logger
        discord_logger = get_logger("jellynouncer.discord")
        discord_logger.info("Notification sent to Discord")
        ```

    Note:
        If the requested logger name doesn't exist, Python's logging system
        will create it automatically. However, it will inherit configuration
        from its parent logger in the hierarchy, so the main logger should
        be configured first with setup_logging().
    """
    return logging.getLogger(name)


def format_bytes(bytes_value: int) -> str:
    """
    Format byte count into human-readable string with appropriate units.

    This utility function converts raw byte counts into user-friendly representations
    using standard binary units (1024-based). This is essential for displaying file
    sizes, memory usage, and network transfer amounts in a way users can understand.

    **Binary vs Decimal Units:**
    This function uses binary units (1024-based) which are standard for:
    - File systems and storage devices
    - Memory measurements (RAM, cache sizes)
    - Most operating system utilities

    Binary: 1 KB = 1024 bytes, 1 MB = 1024 KB, etc.
    (vs Decimal: 1 kB = 1000 bytes - used by some manufacturers)

    **Unit Progression:**
    B → KB → MB → GB → TB → PB
    Each unit is 1024 times larger than the previous one.

    Args:
        bytes_value (int): Number of bytes to format. Can be 0 or positive integer.

    Returns:
        str: Human-readable string with appropriate unit (e.g., "1.5 MB", "250 KB")

    Example:
        ```python
        print(format_bytes(1024))        # "1.0 KB"
        print(format_bytes(1536))        # "1.5 KB"
        print(format_bytes(1048576))     # "1.0 MB"
        print(format_bytes(1073741824))  # "1.0 GB"
        print(format_bytes(500))         # "500 B"
        print(format_bytes(0))           # "0 B"
        ```

    Note:
        This function handles edge cases gracefully:
        - Zero bytes returns "0 B"
        - Negative values are treated as zero
        - Very large values are supported up to petabytes
        - Results are rounded to one decimal place for readability
    """
    if bytes_value <= 0:
        return "0 B"

    # Define units in order from smallest to largest
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

    # Find the appropriate unit by repeatedly dividing by 1024
    size = float(bytes_value)
    unit_index = 0

    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    # Format with one decimal place, but remove trailing zeros
    if size == int(size):
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def sanitize_filename(filename: str, replacement: str = "_") -> str:
    """
    Clean filename for safe filesystem usage by removing/replacing invalid characters.

    This utility function ensures filenames are safe for use across different
    operating systems and filesystems. It removes or replaces characters that
    could cause issues when creating files or directories.

    **Cross-Platform Compatibility:**
    Different operating systems have different rules for valid filenames:
    - Windows: Cannot contain < > : " | ? * or control characters
    - Linux/Unix: More permissive, but some characters still problematic
    - macOS: Similar to Linux but with additional restrictions

    This function creates filenames that work safely on all major platforms.

    **Character Handling:**
    - Invalid characters are replaced with the replacement string
    - Leading/trailing spaces and dots are removed (Windows requirement)
    - Reserved Windows names are handled (CON, PRN, AUX, etc.)
    - Unicode characters are preserved if they're filesystem-safe

    Args:
        filename (str): Original filename to sanitize
        replacement (str): Character to replace invalid characters with (default: "_")

    Returns:
        str: Sanitized filename safe for filesystem use

    Raises:
        ValueError: If filename is empty or becomes empty after sanitization

    Example:
        ```python
        # Remove problematic characters
        safe_name = sanitize_filename("My Movie: The Sequel (2024)")
        print(safe_name)  # "My Movie_ The Sequel (2024)"

        # Handle Windows reserved names
        safe_name = sanitize_filename("CON.txt")
        print(safe_name)  # "CON_.txt"

        # Custom replacement character
        safe_name = sanitize_filename("File<>Name", replacement="-")
        print(safe_name)  # "File--Name"
        ```

    Note:
        This function is conservative - it may replace some characters that
        are valid on your specific system, but the result will work everywhere.
        The goal is cross-platform compatibility rather than maximum permissiveness.
    """
    if not filename or not filename.strip():
        raise ValueError("Filename cannot be empty")

    # Define characters that are invalid on any major filesystem
    invalid_chars = r'<>:"/\|?*'

    # Add control characters (ASCII 0-31) to invalid characters
    invalid_pattern = f'[{re.escape(invalid_chars)}\x00-\x1f]'

    # Replace invalid characters with the replacement string
    sanitized = re.sub(invalid_pattern, replacement, filename)

    # Remove leading/trailing whitespace and dots (Windows requirement)
    sanitized = sanitized.strip(' .')

    # Handle Windows reserved names (case-insensitive)
    reserved_names = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    }

    # Check if the base name (before extension) is reserved
    name_parts = sanitized.rsplit('.', 1)
    base_name = name_parts[0].upper()

    if base_name in reserved_names:
        # Append replacement character to make it safe
        if len(name_parts) == 2:
            sanitized = f"{name_parts[0]}{replacement}.{name_parts[1]}"
        else:
            sanitized = f"{sanitized}{replacement}"

    # Ensure we still have a valid filename after all transformations
    if not sanitized or not sanitized.strip():
        raise ValueError(f"Filename became empty after sanitization: '{filename}'")

    # Limit length to be safe for most filesystems (255 characters is common limit)
    if len(sanitized) > 255:
        name_parts = sanitized.rsplit('.', 1)
        if len(name_parts) == 2:
            # Preserve extension, truncate name
            max_name_length = 255 - len(name_parts[1]) - 1  # -1 for the dot
            sanitized = f"{name_parts[0][:max_name_length]}.{name_parts[1]}"
        else:
            # No extension, just truncate
            sanitized = sanitized[:255]

    return sanitized