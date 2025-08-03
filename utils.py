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
    setup_logging: Configure comprehensive logging with rotation and custom formatting
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
from datetime import datetime, timezone
from pathlib import Path


def setup_logging(log_level: str = "INFO", log_dir: str = "/app/logs") -> logging.Logger:
    """
    Set up comprehensive logging with rotation and custom formatting.

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
    - Console Handler: Shows INFO+ messages for immediate feedback
    - File Handler: Stores all messages with automatic rotation
    - Custom Formatter: Structured format with UTC timestamps
    - Rotation: 10MB per file, 5 backup files (50MB total maximum)

    Args:
        log_level (str): Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            DEBUG provides the most detail, CRITICAL only shows critical errors.
        log_dir (str): Directory path where log files will be stored.
            Created automatically if it doesn't exist.

    Returns:
        logging.Logger: Configured logger instance ready for use throughout the application

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
    # Create logs directory if it doesn't exist
    # parents=True creates parent directories, exist_ok=True prevents errors if it exists
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    class BracketFormatter(logging.Formatter):
        """
        Custom log formatter that uses brackets for structured, readable output.

        This nested class creates a custom formatter that generates consistent,
        structured log messages. The bracket format makes it easy to parse logs
        programmatically while remaining human-readable.

        **Formatter Benefits:**
        - Consistent timestamp format (UTC for server deployments)
        - Structured format that's easy to parse with tools
        - Component identification for debugging
        - User context for multi-user scenarios

        **Format Structure:**
        `[timestamp] [user] [level] [component] message`
        
        Where:
        - timestamp: UTC time in ISO format for consistency across time zones
        - user: User context (defaults to 'system' for service operations)
        - level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        - component: Logger name for identifying message source
        - message: The actual log message content
        """

        def format(self, record):
            """
            Format a log record into structured bracket format.

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
    logger = logging.getLogger("jellynouncer")  # Changed from "jellynotify" to "jellynouncer"
    logger.setLevel(getattr(logging, log_level.upper()))

    # Clear any existing handlers to prevent duplicate logs during testing/development
    # This is important if setup_logging() is called multiple times
    logger.handlers.clear()

    # Console handler for immediate feedback during development and debugging
    # Shows messages on the terminal/console for real-time monitoring
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Only show INFO+ on console to reduce noise
    console_handler.setFormatter(BracketFormatter())
    logger.addHandler(console_handler)

    # Rotating file handler to prevent logs from consuming unlimited disk space
    # This is crucial for production deployments that run continuously
    file_handler = logging.handlers.RotatingFileHandler(
        filename=Path(log_dir) / "jellynouncer.log",  # Changed from "jellynotify.log"
        maxBytes=10 * 1024 * 1024,    # 10MB per file (reasonable size for analysis)
        backupCount=5,                # Keep 5 backup files (jellynouncer.log.1, .2, etc.)
        encoding='utf-8',             # Ensure proper encoding for international characters
        mode='a'                      # Append mode - preserves logs across service restarts
    )
    file_handler.setLevel(getattr(logging, log_level.upper()))  # Use specified log level for files
    file_handler.setFormatter(BracketFormatter())
    logger.addHandler(file_handler)

    # Disable uvicorn's access logger to avoid duplication with our custom logging
    # Uvicorn is the ASGI server that runs FastAPI applications
    logging.getLogger("uvicorn.access").disabled = True

    # Log the configuration for verification and debugging
    # This helps administrators verify logging is set up correctly
    logger.info("=" * 60)
    logger.info("Jellynouncer Logging Configuration")  # Updated project name
    logger.info("=" * 60)
    logger.info(f"Log Level: {log_level.upper()}")
    logger.info(f"Log Directory: {log_dir}")
    logger.info(f"Main Log File: {Path(log_dir) / 'jellynouncer.log'}")  # Updated filename
    logger.info(f"Max Log Size: 10MB per file")
    logger.info(f"Backup Count: 5 files")
    logger.info(f"Total Storage: 50MB maximum")
    logger.info(f"Total Handlers: {len(logger.handlers)}")
    
    # List each handler for diagnostic purposes
    for i, handler in enumerate(logger.handlers):
        logger.info(f"Handler {i + 1}: {type(handler).__name__} - Level: {logging.getLevelName(handler.level)}")
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
        str: Formatted string with value and appropriate unit (e.g., "1.5 GB", "750 MB")

    Example:
        ```python
        # Common file sizes
        print(format_bytes(0))           # "0 B"
        print(format_bytes(512))         # "512 B"
        print(format_bytes(1024))        # "1.0 KB"
        print(format_bytes(1536))        # "1.5 KB"
        print(format_bytes(1048576))     # "1.0 MB"
        print(format_bytes(1073741824))  # "1.0 GB"

        # Real-world usage
        file_size = 2048576000
        print(f"Movie file size: {format_bytes(file_size)}")  # "Movie file size: 1.9 GB"

        # Database usage
        db_size = 524288000
        print(f"Database size: {format_bytes(db_size)}")      # "Database size: 500.0 MB"
        ```

    Note:
        The function handles edge cases gracefully:
        - Zero bytes returns "0 B"
        - Very large values are supported up to petabytes
        - Decimal precision is limited to 1 place for readability
        - Bytes are shown as integers (no decimal places)
    """
    if bytes_value == 0:
        return "0 B"

    # Define unit progression - each unit is 1024x larger than previous
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    unit_index = 0
    size = float(bytes_value)

    # Convert to the largest appropriate unit
    # Stop when size is less than 1024 or we've reached the largest unit
    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    # Format based on unit - bytes as integers, others with 1 decimal place
    if unit_index == 0:  # Bytes
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename for safe filesystem usage across different operating systems.

    This function removes or replaces characters that are problematic for filesystems,
    ensuring that generated filenames work correctly on Windows, Linux, and macOS.
    This is important when creating files based on media titles or user input.

    **Why Filename Sanitization is Needed:**
    Different operating systems have different restrictions on filename characters:
    - Windows: Cannot use < > : " | ? * \ /
    - Linux/macOS: Cannot use / (and \ is legal but confusing)
    - All systems: Control characters and some Unicode can cause issues

    **Common Problematic Characters:**
    - `/` and `\`: Directory separators
    - `:`: Drive separator on Windows, time separator
    - `*` and `?`: Wildcards in many systems
    - `"`: Quote character that can break commands
    - `|`: Pipe character used in command lines
    - `<` and `>`: Redirection operators

    **Sanitization Strategy:**
    This function replaces problematic characters with underscores, which are
    safe on all major filesystems and maintain filename readability.

    Args:
        filename (str): Original filename that may contain problematic characters

    Returns:
        str: Sanitized filename safe for use on all major filesystems

    Example:
        ```python
        # Movie titles with problematic characters
        sanitize_filename("Movie: The Sequel (2023)")     # "Movie_ The Sequel (2023)"
        sanitize_filename("TV Show/Episode 1")            # "TV Show_Episode 1"
        sanitize_filename('File "with quotes"')           # "File _with quotes_"
        sanitize_filename("Data<File>Name")               # "Data_File_Name"
        
        # Real-world usage for cache files
        movie_title = "The Matrix: Reloaded"
        cache_filename = f"{sanitize_filename(movie_title)}.json"
        # Result: "The Matrix_ Reloaded.json"
        
        # Log file naming
        series_name = "TV Show/Season 1"
        log_file = f"{sanitize_filename(series_name)}_processing.log"
        # Result: "TV Show_Season 1_processing.log"
        ```

    Note:
        This function is conservative in its approach - it replaces characters
        rather than removing them to maintain filename readability. The resulting
        filenames may look slightly different but will be unambiguous and safe.

        For maximum compatibility, the function also limits filename length
        and handles edge cases like empty strings or strings with only
        problematic characters.
    """
    if not filename:
        return "unnamed_file"

    # Replace problematic characters with underscores
    # This list covers the most common filesystem-unsafe characters
    problematic_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    
    sanitized = filename
    for char in problematic_chars:
        sanitized = sanitized.replace(char, '_')
    
    # Remove or replace other potentially problematic characters
    # Control characters (ASCII 0-31) can cause issues
    sanitized = ''.join(char if ord(char) >= 32 else '_' for char in sanitized)
    
    # Remove leading/trailing whitespace and dots (problematic on Windows)
    sanitized = sanitized.strip(' .')
    
    # Ensure filename isn't empty after sanitization
    if not sanitized:
        return "unnamed_file"
    
    # Limit length to prevent filesystem issues (255 chars is common limit)
    # Leave room for file extensions
    max_length = 200
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip(' .')
    
    return sanitized


def format_duration(seconds: int) -> str:
    """
    Format duration in seconds to human-readable time format.

    This utility function converts duration values (typically from media files)
    into readable time formats. Useful for displaying runtime information in
    Discord notifications and logs.

    **Time Format Logic:**
    - Less than 60 seconds: "45s"
    - Less than 1 hour: "5m 30s"  
    - 1 hour or more: "2h 15m 30s"
    - Omits zero values for cleaner display

    Args:
        seconds (int): Duration in seconds

    Returns:
        str: Formatted duration string

    Example:
        ```python
        # Various duration examples
        print(format_duration(45))      # "45s"
        print(format_duration(330))     # "5m 30s" 
        print(format_duration(3661))    # "1h 1m 1s"
        print(format_duration(7200))    # "2h"
        print(format_duration(0))       # "0s"

        # Real-world usage with media
        movie_runtime = 8100  # seconds from Jellyfin API
        print(f"Runtime: {format_duration(movie_runtime)}")  # "Runtime: 2h 15m"
        ```

    Note:
        This function is designed for media runtime display where hours are
        the largest practical unit. For longer durations (days, weeks), 
        consider using a different formatting approach.
    """
    if seconds <= 0:
        return "0s"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if remaining_seconds > 0 or not parts:  # Show seconds if it's the only unit
        parts.append(f"{remaining_seconds}s")
    
    return " ".join(parts)


def truncate_string(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate string to specified length with optional suffix.

    This utility function safely truncates long strings for display in
    Discord embeds, log messages, and other contexts where length limits
    are important. It preserves readability by adding ellipsis or other
    indicators when truncation occurs.

    **Truncation Strategy:**
    - If text fits within limit: return unchanged
    - If text exceeds limit: truncate and add suffix
    - Suffix counts toward total length limit
    - Handles edge cases like empty strings and very short limits

    Args:
        text (str): Text string to potentially truncate
        max_length (int): Maximum allowed length including suffix
        suffix (str): String to append when truncation occurs (default: "...")

    Returns:
        str: Original text or truncated version with suffix

    Example:
        ```python
        # Basic truncation
        long_text = "This is a very long description that needs truncation"
        print(truncate_string(long_text, 20))  # "This is a very lo..."

        # Custom suffix
        print(truncate_string(long_text, 25, " [more]"))  # "This is a very [more]"

        # Discord embed limits
        description = "Very long movie plot summary..."
        discord_desc = truncate_string(description, 4096)  # Discord embed limit

        # Log message truncation
        error_details = "Extremely detailed error information..."
        log_message = truncate_string(error_details, 200, " [truncated]")
        ```

    Note:
        This function is commonly used for:
        - Discord embed field values (limited to specific lengths)
        - Log message formatting to prevent excessive output
        - Database field truncation to prevent constraint violations  
        - User interface display where space is limited
    """
    if not text or len(text) <= max_length:
        return text
    
    if max_length <= len(suffix):
        # If max_length is too small for suffix, just return truncated text
        return text[:max_length]
    
    # Truncate text to make room for suffix
    truncated_length = max_length - len(suffix)
    return text[:truncated_length] + suffix