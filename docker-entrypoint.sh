#!/bin/bash
#
# JellyNotify Docker Container Initialization Script
# Entrypoint script with advanced error handling and logging
#
# Compatible with: Debian 12 (Bookworm) - Bash 5.2.15+
# Author: Mark Newton
# Version: 1.0.0
#
# This script initializes the JellyNotify Docker container environment with:
# - Multi-level logging system with automatic rotation
# - Comprehensive error handling and recovery
# - JSON configuration management with validation
# - Efficient file operations using Bash 5.2 features
# - Advanced permission management
# - Performance monitoring and diagnostics
#

#=============================================================================
# USER MANAGEMENT FUNCTIONS
#=============================================================================

# Create and configure user for running the application
setup_application_user() {
    local component="USER"
    local puid="${PUID:-1000}"
    local pgid="${PGID:-1000}"

    log_info "Setting up application user with UID:GID ${puid}:${pgid}" "${component}"

    # Create group if it doesn't exist
    if ! getent group "${pgid}" >/dev/null 2>&1; then
        if groupadd -g "${pgid}" jellynotify 2>/dev/null; then
            log_success "Created group 'jellynotify' with GID ${pgid}" "${component}"
        else
            log_warning "Could not create group with GID ${pgid}, using existing group" "${component}"
        fi
    else
        local existing_group
        existing_group=$(getent group "${pgid}" | cut -d: -f1)
        log_debug "Using existing group '${existing_group}' with GID ${pgid}" "${component}"
    fi

    # Create user if it doesn't exist
    if ! getent passwd "${puid}" >/dev/null 2>&1; then
        if useradd -u "${puid}" -g "${pgid}" -d /app -s /bin/bash -M jellynotify 2>/dev/null; then
            log_success "Created user 'jellynotify' with UID ${puid}" "${component}"
        else
            log_error "Could not create user with UID ${puid}" "${component}"
            return 1
        fi
    else
        local existing_user
        existing_user=$(getent passwd "${puid}" | cut -d: -f1)
        log_debug "Using existing user '${existing_user}' with UID ${puid}" "${component}"
    fi

    # Verify user can access application directory
    local test_user
    test_user=$(getent passwd "${puid}" | cut -d: -f1)

    if [[ -n "${test_user}" ]]; then
        # Test write access to app directory
        if su - "${test_user}" -c "test -w /app" 2>/dev/null; then
            log_success "User '${test_user}' has proper access to /app directory" "${component}"
        else
            log_warning "User '${test_user}' may not have proper access to /app directory" "${component}"
        fi

        # Export the username for use in exec
        export APP_USER="${test_user}"
        log_debug "Application will run as user: ${APP_USER}" "${component}"
        return 0
    else
        log_error "Failed to verify created user" "${component}"
        return 1
    fi
}

# Switch to application user and execute command
# Switch to application user and execute command
exec_as_app_user() {
    local component="EXEC"

    if [[ -n "${APP_USER:-}" ]]; then
        log_info "Switching to user '${APP_USER}' and executing: $*" "${component}"

        # Change to app directory first
        cd /app || {
            log_error "Failed to change to /app directory" "${component}"
            exit 1
        }

        # Use gosu (designed specifically for this use case)
        if command -v gosu >/dev/null 2>&1; then
            log_debug "Using gosu to execute command as ${APP_USER}" "${component}"
            exec gosu "${APP_USER}" "$@"
        # Fallback to runuser (available in Debian 12)
        elif command -v runuser >/dev/null 2>&1; then
            log_debug "Using runuser to execute command as ${APP_USER}" "${component}"
            exec runuser -u "${APP_USER}" -- "$@"
        # Last resort: su with proper syntax
        else
            log_debug "Using su to execute command as ${APP_USER}" "${component}"
            exec su "${APP_USER}" -c 'exec "$@"' -- bash "$@"
        fi
    else
        log_warning "No application user set, executing as root: $*" "${component}"
        exec "$@"
    fi
}

#=============================================================================
# BASH CONFIGURATION AND STRICT MODE
#=============================================================================

# Enable Bash 5.2 strict mode for enterprise error handling
set -euo pipefail

# Enable extended debugging if DEBUG environment variable is set
# This uses Bash 5.2's enhanced debugging capabilities
if [[ "${DEBUG:-false}" == "true" ]]; then
    set -x
    export PS4='+(${BASH_SOURCE}:${LINENO}): ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'
fi

# Set Internal Field Separator to default for security
IFS=$' \t\n'

# Ensure consistent locale for predictable behavior
export LC_ALL=C
export LANG=C

#=============================================================================
# CONSTANTS AND GLOBAL VARIABLES
#=============================================================================

# Script metadata
readonly SCRIPT_NAME="docker-entrypoint.sh"
readonly SCRIPT_VERSION="1.0.0"
readonly SCRIPT_PID=$$

# Logging configuration
readonly LOG_TIMESTAMP_FORMAT="+%Y-%m-%d %H:%M:%S"
readonly DEBUG_LOG_FILE="/app/logs/debug.log"

# Application directories
readonly APP_ROOT="/app"
readonly CONFIG_DIR="${APP_ROOT}/config"
readonly TEMPLATES_DIR="${APP_ROOT}/templates"
readonly DATA_DIR="${APP_ROOT}/data"
readonly LOGS_DIR="${APP_ROOT}/logs"
readonly SCRIPTS_DIR="${APP_ROOT}/scripts"
readonly DEFAULTS_DIR="${APP_ROOT}/defaults"

# File paths
readonly CONFIG_FILE="${CONFIG_DIR}/config.json"
readonly DEFAULT_CONFIG_FILE="${DEFAULTS_DIR}/config.json"
readonly TEMP_DIR="/tmp/jellynotify-init-$"

# Process tracking
declare -g CLEANUP_REGISTERED=false
declare -g TEMP_FILES=()
declare -g BACKGROUND_PIDS=()

# Performance tracking
declare -g START_TIME
START_TIME=$(date +%s.%N)

# Log level constants (using integers for performance)
readonly LOG_LEVEL_DEBUG=10
readonly LOG_LEVEL_INFO=20
readonly LOG_LEVEL_SUCCESS=25
readonly LOG_LEVEL_WARNING=30
readonly LOG_LEVEL_ERROR=40
readonly LOG_LEVEL_FAIL=45
readonly LOG_LEVEL_CRITICAL=50

# Color constants for terminal output
readonly COLOR_RESET='\033[0m'
readonly COLOR_RED='\033[0;31m'
readonly COLOR_GREEN='\033[0;32m'
readonly COLOR_YELLOW='\033[0;33m'
readonly COLOR_BLUE='\033[0;34m'
readonly COLOR_MAGENTA='\033[0;35m'
readonly COLOR_CYAN='\033[0;36m'
readonly COLOR_WHITE='\033[0;37m'
readonly COLOR_BOLD='\033[1m'

#=============================================================================
# ADVANCED LOGGING SYSTEM
#=============================================================================

# Initialize debug logging if DEBUG is enabled
init_debug_logging() {
    if [[ "${DEBUG:-false}" == "true" ]]; then
        # Ensure logs directory exists
        mkdir -p "$(dirname "${DEBUG_LOG_FILE}")" 2>/dev/null || true

        # Initialize debug log file with header
        {
            echo "========================================================================"
            echo "JellyNotify Docker Entrypoint Debug Log - Started $(date)"
            echo "Script: ${SCRIPT_NAME} v${SCRIPT_VERSION}"
            echo "PID: ${SCRIPT_PID}"
            echo "Host: $(hostname 2>/dev/null || echo 'unknown')"
            echo "Debug Mode: ENABLED"
            echo "========================================================================"
        } > "${DEBUG_LOG_FILE}" 2>/dev/null || true

        # Set proper ownership and permissions on debug log
        if [[ -n "${PUID:-}" && -n "${PGID:-}" ]]; then
            safe_chown "${DEBUG_LOG_FILE}" "${PUID}:${PGID}" "DEBUG"
        fi
        safe_chmod "${DEBUG_LOG_FILE}" "644" "DEBUG"
    fi
}

# Advanced logging function with optional debug file output
# Parameters: level, message, [component]
log_message() {
    local level="${1:-INFO}"
    local message="${2:-}"
    local component="${3:-MAIN}"

    # Validate inputs
    [[ -n "${message}" ]] || return 1

    # Get current timestamp with high precision
    local timestamp
    timestamp=$(date "${LOG_TIMESTAMP_FORMAT}")

    # Format log entry
    local log_entry="[${timestamp}] [${level^^}] [${component}] ${message}"

    # Get color for terminal output
    local color
    color=$(get_log_color "${level}")

    # Always output to stderr with color
    echo -e "${color}${log_entry}${COLOR_RESET}" >&2

    # Also write to debug log file if DEBUG mode is enabled
    if [[ "${DEBUG:-false}" == "true" ]]; then
        # Write to debug log file without color codes
        echo "${log_entry}" >> "${DEBUG_LOG_FILE}" 2>/dev/null || true
    fi
}

# Get color code for log level
get_log_color() {
    local level="$1"
    case "${level^^}" in
        DEBUG)    echo "${COLOR_CYAN}" ;;
        INFO)     echo "${COLOR_BLUE}" ;;
        SUCCESS)  echo "${COLOR_GREEN}" ;;
        WARNING)  echo "${COLOR_YELLOW}" ;;
        ERROR)    echo "${COLOR_RED}" ;;
        FAIL)     echo "${COLOR_MAGENTA}" ;;
        CRITICAL) echo "${COLOR_BOLD}${COLOR_RED}" ;;
        *)        echo "${COLOR_RESET}" ;;
    esac
}

# Convenience functions for different log levels
log_debug()    { log_message "DEBUG" "$1" "${2:-MAIN}"; }
log_info()     { log_message "INFO" "$1" "${2:-MAIN}"; }
log_success()  { log_message "SUCCESS" "$1" "${2:-MAIN}"; }
log_warning()  { log_message "WARNING" "$1" "${2:-MAIN}"; }
log_error()    { log_message "ERROR" "$1" "${2:-MAIN}"; }
log_fail()     { log_message "FAIL" "$1" "${2:-MAIN}"; }
log_critical() { log_message "CRITICAL" "$1" "${2:-MAIN}"; }

#=============================================================================
# UTILITY AND HELPER FUNCTIONS
#=============================================================================

# Register cleanup function to run on script exit
# This uses Bash 5.2's enhanced trap handling
register_cleanup() {
    if [[ "${CLEANUP_REGISTERED}" == "false" ]]; then
        # Use Bash 5.2's improved signal handling
        trap 'cleanup_on_exit $?' EXIT
        trap 'cleanup_on_signal SIGINT' SIGINT
        trap 'cleanup_on_signal SIGTERM' SIGTERM

        CLEANUP_REGISTERED=true
        log_debug "Cleanup handlers registered" "INIT"
    fi
}

# Cleanup function called on script exit
cleanup_on_exit() {
    local exit_code="$1"
    local end_time
    end_time=$(date +%s.%N)

    # Calculate execution time using Bash arithmetic
    local execution_time
    execution_time=$(awk "BEGIN {printf \"%.3f\", ${end_time} - ${START_TIME}}")

    log_info "Script execution completed in ${execution_time}s with exit code ${exit_code}" "CLEANUP"

    # Write final debug log entry if debug mode is enabled
    if [[ "${DEBUG:-false}" == "true" ]]; then
        {
            echo ""
            echo "========================================================================"
            echo "Script execution completed at $(date)"
            echo "Exit code: ${exit_code}"
            echo "Execution time: ${execution_time}s"
            echo "========================================================================"
        } >> "${DEBUG_LOG_FILE}" 2>/dev/null || true
    fi

    # Clean up temporary files
    if [[ ${#TEMP_FILES[@]} -gt 0 ]]; then
        log_debug "Cleaning up ${#TEMP_FILES[@]} temporary files" "CLEANUP"
        for temp_file in "${TEMP_FILES[@]}"; do
            [[ -e "${temp_file}" ]] && rm -rf "${temp_file}" 2>/dev/null || true
        done
    fi

    # Clean up temporary directory
    [[ -d "${TEMP_DIR}" ]] && rm -rf "${TEMP_DIR}" 2>/dev/null || true

    # Kill background processes if any
    if [[ ${#BACKGROUND_PIDS[@]} -gt 0 ]]; then
        log_debug "Terminating ${#BACKGROUND_PIDS[@]} background processes" "CLEANUP"
        for pid in "${BACKGROUND_PIDS[@]}"; do
            kill "${pid}" 2>/dev/null || true
        done
    fi
}

# Cleanup function called on signal
cleanup_on_signal() {
    local signal="$1"
    log_warning "Received signal ${signal}, performing cleanup" "SIGNAL"
    exit 130
}

# Create a secure temporary file and track it for cleanup
create_temp_file() {
    local prefix="${1:-jellynotify}"
    local temp_file

    # Ensure temp directory exists
    mkdir -p "${TEMP_DIR}"

    # Create secure temporary file using Bash 5.2's improved mktemp handling
    temp_file=$(mktemp "${TEMP_DIR}/${prefix}.XXXXXXXXXX")

    # Track for cleanup
    TEMP_FILES+=("${temp_file}")

    echo "${temp_file}"
}

# Validate if a file contains valid JSON using jq
validate_json_file() {
    local file_path="$1"
    local component="${2:-JSON}"

    if [[ ! -f "${file_path}" ]]; then
        log_error "JSON file does not exist: ${file_path}" "${component}"
        return 1
    fi

    if ! jq empty "${file_path}" 2>/dev/null; then
        log_error "Invalid JSON in file: ${file_path}" "${component}"
        return 1
    fi

    log_debug "JSON validation successful: ${file_path}" "${component}"
    return 0
}

# Safely update file ownership with error handling
safe_chown() {
    local target="$1"
    local ownership="$2"
    local component="${3:-PERM}"

    if [[ ! -e "${target}" ]]; then
        log_warning "Cannot change ownership of non-existent path: ${target}" "${component}"
        return 1
    fi

    if chown "${ownership}" "${target}" 2>/dev/null; then
        log_debug "Ownership set to ${ownership} on ${target}" "${component}"
        return 0
    else
        log_warning "Failed to set ownership ${ownership} on ${target}" "${component}"
        return 1
    fi
}

# Safely update file permissions with error handling
safe_chmod() {
    local target="$1"
    local permissions="$2"
    local component="${3:-PERM}"

    if [[ ! -e "${target}" ]]; then
        log_warning "Cannot change permissions of non-existent path: ${target}" "${component}"
        return 1
    fi

    if chmod "${permissions}" "${target}" 2>/dev/null; then
        log_debug "Permissions set to ${permissions} on ${target}" "${component}"
        return 0
    else
        log_warning "Failed to set permissions ${permissions} on ${target}" "${component}"
        return 1
    fi
}

# Measure execution time of a command or function
measure_execution_time() {
    local start_time end_time execution_time
    start_time=$(date +%s.%N)

    # Execute the command
    "$@"
    local exit_code=$?

    end_time=$(date +%s.%N)
    execution_time=$(awk "BEGIN {printf \"%.3f\", ${end_time} - ${start_time}}")

    log_debug "Command '$*' executed in ${execution_time}s" "PERF"
    return ${exit_code}
}

#=============================================================================
# DIRECTORY MANAGEMENT FUNCTIONS
#=============================================================================

# Create required directories with proper error handling
# This function leverages Bash 5.2's improved array handling
create_required_directories() {
    local component="DIRS"
    log_info "Creating required directory structure" "${component}"

    # Define required directories using an associative array for better organization
    declare -A required_dirs=(
        ["${CONFIG_DIR}"]="Configuration directory"
        ["${TEMPLATES_DIR}"]="Templates directory"
        ["${DATA_DIR}"]="Data directory"
        ["${LOGS_DIR}"]="Application logs directory"
        ["${SCRIPTS_DIR}"]="Scripts directory"
    )

    local created_count=0
    local failed_count=0

    # Process each directory with detailed logging
    for dir_path in "${!required_dirs[@]}"; do
        local description="${required_dirs[${dir_path}]}"

        if [[ -d "${dir_path}" ]]; then
            log_debug "${description} already exists: ${dir_path}" "${component}"
        else
            if mkdir -p "${dir_path}" 2>/dev/null; then
                log_success "${description} created: ${dir_path}" "${component}"
                ((created_count++))
            else
                log_error "Failed to create ${description}: ${dir_path}" "${component}"
                ((failed_count++))
            fi
        fi
    done

    # Summary logging
    if (( failed_count > 0 )); then
        log_error "Directory creation failed: ${failed_count} errors, ${created_count} created" "${component}"
        return 1
    else
        log_success "Directory structure ready: ${created_count} directories created" "${component}"
        return 0
    fi
}

#=============================================================================
# CONFIGURATION MANAGEMENT FUNCTIONS
#=============================================================================

# Advanced JSON configuration update using jq with comprehensive error handling
# This function uses Bash 5.2's enhanced command substitution for better performance
update_config_json() {
    local config_file="$1"
    local component="CONFIG"

    log_info "Updating configuration file with environment variables" "${component}"

    # Debug: Print environment variables
    log_debug "Environment variables being used:" "${component}"
    log_debug "  JELLYFIN_SERVER_URL='${JELLYFIN_SERVER_URL:-<unset>}'" "${component}"
    log_debug "  JELLYFIN_API_KEY='${JELLYFIN_API_KEY:-<unset>}'" "${component}"
    log_debug "  JELLYFIN_USER_ID='${JELLYFIN_USER_ID:-<unset>}'" "${component}"
    log_debug "  DISCORD_WEBHOOK_URL='${DISCORD_WEBHOOK_URL:-<unset>}'" "${component}"
    log_debug "  DISCORD_WEBHOOK_URL_MOVIES='${DISCORD_WEBHOOK_URL_MOVIES:-<unset>}'" "${component}"
    log_debug "  DISCORD_WEBHOOK_URL_TV='${DISCORD_WEBHOOK_URL_TV:-<unset>}'" "${component}"
    log_debug "  DISCORD_WEBHOOK_URL_MUSIC='${DISCORD_WEBHOOK_URL_MUSIC:-<unset>}'" "${component}"

    # Validate input file
    if ! validate_json_file "${config_file}" "${component}"; then
        log_error "Configuration file validation failed" "${component}"
        return 1
    fi

    # Debug: Show original config before modification
    log_debug "Original config file content:" "${component}"
    jq . "${config_file}" 2>/dev/null | head -20 | while IFS= read -r line; do
        log_debug "  ${line}" "${component}"
    done

    # Create secure temporary file for atomic updates
    local temp_config
    temp_config=$(create_temp_file "config")

    # Prepare environment variables with null fallbacks for jq
    local env_vars=(
        "jellyfin_url:${JELLYFIN_SERVER_URL:-null}"
        "api_key:${JELLYFIN_API_KEY:-null}"
        "user_id:${JELLYFIN_USER_ID:-null}"
        "webhook_url:${DISCORD_WEBHOOK_URL:-null}"
        "movies_url:${DISCORD_WEBHOOK_URL_MOVIES:-null}"
        "tv_url:${DISCORD_WEBHOOK_URL_TV:-null}"
        "music_url:${DISCORD_WEBHOOK_URL_MUSIC:-null}"
        "omdb_key:${OMDB_API_KEY:-null}"
        "tmdb_key:${TMDB_API_KEY:-null}"
        "tvdb_key:${TVDB_API_KEY:-null}"
    )

    # Build jq arguments array for better maintainability
    local jq_args=()
    for env_var in "${env_vars[@]}"; do
        local var_name="${env_var%%:*}"
        local var_value="${env_var#*:}"
        jq_args+=(--arg "${var_name}" "${var_value}")
    done

    # Execute jq transformation with comprehensive error handling
    # Using Bash 5.2's improved process substitution
    if jq "${jq_args[@]}" '
        # Update Jellyfin configuration
        .jellyfin.server_url = (if $jellyfin_url != "null" then $jellyfin_url else .jellyfin.server_url end) |
        .jellyfin.api_key = (if $api_key != "null" then $api_key else .jellyfin.api_key end) |
        .jellyfin.user_id = (if $user_id != "null" then $user_id else .jellyfin.user_id end) |

        # Update Discord webhook URLs and enable them if URLs are provided
        .discord.webhooks.default.url = (if $webhook_url != "null" then $webhook_url else .discord.webhooks.default.url end) |
        .discord.webhooks.default.enabled = (if $webhook_url != "null" then true else .discord.webhooks.default.enabled end) |

        .discord.webhooks.movies.url = (if $movies_url != "null" then $movies_url else .discord.webhooks.movies.url end) |
        .discord.webhooks.movies.enabled = (if $movies_url != "null" then true else .discord.webhooks.movies.enabled end) |

        .discord.webhooks.tv.url = (if $tv_url != "null" then $tv_url else .discord.webhooks.tv.url end) |
        .discord.webhooks.tv.enabled = (if $tv_url != "null" then true else .discord.webhooks.tv.enabled end) |

        .discord.webhooks.music.url = (if $music_url != "null" then $music_url else .discord.webhooks.music.url end) |
        .discord.webhooks.music.enabled = (if $music_url != "null" then true else .discord.webhooks.music.enabled end) |

        # Enable routing if any specific webhooks are configured
        .discord.routing.enabled = (if ($movies_url != "null" or $tv_url != "null" or $music_url != "null") then true else .discord.routing.enabled end) |

        # Update rating service API keys and auto-enable only if previously null
        .rating_services.omdb.api_key = (if $omdb_key != "null" then $omdb_key else .rating_services.omdb.api_key end) |
        .rating_services.omdb.enabled = (if ($omdb_key != "null" and (.rating_services.omdb.api_key == null or .rating_services.omdb.api_key == "")) then true else .rating_services.omdb.enabled end) |

        .rating_services.tmdb.api_key = (if $tmdb_key != "null" then $tmdb_key else .rating_services.tmdb.api_key end) |
        .rating_services.tmdb.enabled = (if ($tmdb_key != "null" and (.rating_services.tmdb.api_key == null or .rating_services.tmdb.api_key == "")) then true else .rating_services.tmdb.enabled end) |

        .rating_services.tvdb.api_key = (if $tvdb_key != "null" then $tvdb_key else .rating_services.tvdb.api_key end) |
        .rating_services.tvdb.enabled = (if ($tvdb_key != "null" and (.rating_services.tvdb.api_key == null or .rating_services.tvdb.api_key == "")) then true else .rating_services.tvdb.enabled end)
    ' "${config_file}" > "${temp_config}" 2>/dev/null; then

        # Validate the generated JSON
        if validate_json_file "${temp_config}" "${component}"; then
            # Debug: Show what changes were made
            log_debug "Configuration changes made:" "${component}"
            if command -v diff >/dev/null 2>&1; then
                diff -u "${config_file}" "${temp_config}" | head -20 | while IFS= read -r line; do
                    log_debug "  ${line}" "${component}"
                done
            fi

            # Atomic update using mv for consistency
            if mv "${temp_config}" "${config_file}"; then
                log_success "Configuration updated successfully using jq" "${component}"

                # Set proper ownership and permissions on the updated config file
                if [[ -n "${PUID:-}" && -n "${PGID:-}" ]]; then
                    safe_chown "${config_file}" "${PUID}:${PGID}" "${component}"
                fi
                safe_chmod "${config_file}" "644" "${component}"

                # Debug: Show final config
                log_debug "Final config file content:" "${component}"
                jq . "${config_file}" 2>/dev/null | head -20 | while IFS= read -r line; do
                    log_debug "  ${line}" "${component}"
                done

                # Debug: Force sync to ensure file is written
                sync 2>/dev/null || true

                # Debug: Verify file was actually written
                sleep 0.1  # Brief pause to ensure filesystem sync
                if [[ -f "${config_file}" ]]; then
                    local file_size
                    file_size=$(stat -c%s "${config_file}" 2>/dev/null || echo "0")
                    log_debug "File verification after write: ${file_size} bytes" "${component}"
                else
                    log_error "Configuration file disappeared after write!" "${component}"
                fi

                return 0
            else
                log_error "Failed to replace configuration file" "${component}"
                return 1
            fi
        else
            log_error "Generated configuration is invalid JSON" "${component}"
            return 1
        fi
    else
        log_error "jq transformation failed" "${component}"
        return 1
    fi
}

# Setup initial configuration with fallback and validation
setup_configuration() {
    local component="CONFIG"
    log_info "Setting up configuration files" "${component}"

    # Debug: Check file existence
    log_debug "Checking configuration files:" "${component}"
    log_debug "  Config file exists: $([[ -f "${CONFIG_FILE}" ]] && echo "YES" || echo "NO") - ${CONFIG_FILE}" "${component}"
    log_debug "  Default config exists: $([[ -f "${DEFAULT_CONFIG_FILE}" ]] && echo "YES" || echo "NO") - ${DEFAULT_CONFIG_FILE}" "${component}"

    # Check if configuration file exists
    if [[ ! -f "${CONFIG_FILE}" ]]; then
        log_info "Configuration file not found, copying default" "${component}"

        # Verify default configuration exists
        if [[ ! -f "${DEFAULT_CONFIG_FILE}" ]]; then
            log_critical "Default configuration file not found: ${DEFAULT_CONFIG_FILE}" "${component}"

            # Debug: List contents of defaults directory
            log_debug "Contents of ${DEFAULTS_DIR}:" "${component}"
            if [[ -d "${DEFAULTS_DIR}" ]]; then
                find "${DEFAULTS_DIR}" -type f 2>/dev/null | while IFS= read -r file; do
                    log_debug "  Found file: ${file}" "${component}"
                done
            else
                log_debug "  Directory does not exist: ${DEFAULTS_DIR}" "${component}"
            fi

            return 1
        fi

        # Copy default configuration
        if cp "${DEFAULT_CONFIG_FILE}" "${CONFIG_FILE}"; then
            log_success "Default configuration copied successfully" "${component}"

            # Set proper ownership on the copied config file
            if [[ -n "${PUID:-}" && -n "${PGID:-}" ]]; then
                safe_chown "${CONFIG_FILE}" "${PUID}:${PGID}" "${component}"
            fi
            # Set proper permissions for config file
            safe_chmod "${CONFIG_FILE}" "644" "${component}"
        else
            log_error "Failed to copy default configuration" "${component}"
            return 1
        fi
    else
        log_info "Configuration file exists, will update with environment variables" "${component}"
    fi

    # Update configuration with environment variables
    if measure_execution_time update_config_json "${CONFIG_FILE}"; then
        log_success "Configuration setup completed successfully" "${component}"

        # Debug: Verify the final configuration file
        log_debug "Final verification of configuration file:" "${component}"
        log_debug "  File exists: $([[ -f "${CONFIG_FILE}" ]] && echo "YES" || echo "NO")" "${component}"
        log_debug "  File size: $(stat -c%s "${CONFIG_FILE}" 2>/dev/null || echo "unknown") bytes" "${component}"
        log_debug "  File permissions: $(stat -c%a "${CONFIG_FILE}" 2>/dev/null || echo "unknown")" "${component}"
        log_debug "  File owner: $(stat -c%U:%G "${CONFIG_FILE}" 2>/dev/null || echo "unknown")" "${component}"

        # Debug: Show specific fields that should have been modified
        if command -v jq >/dev/null 2>&1; then
            log_debug "Key configuration values after modification:" "${component}"
            log_debug "  jellyfin.server_url: $(jq -r '.jellyfin.server_url' "${CONFIG_FILE}" 2>/dev/null || echo "error")" "${component}"
            log_debug "  jellyfin.api_key: $(jq -r '.jellyfin.api_key' "${CONFIG_FILE}" 2>/dev/null || echo "error")" "${component}"
            log_debug "  discord.webhooks.default.url: $(jq -r '.discord.webhooks.default.url' "${CONFIG_FILE}" 2>/dev/null || echo "error")" "${component}"
            log_debug "  discord.webhooks.default.enabled: $(jq -r '.discord.webhooks.default.enabled' "${CONFIG_FILE}" 2>/dev/null || echo "error")" "${component}"
        fi

        # Debug: Check if this is really the mounted file
        log_debug "Mount point verification:" "${component}"
        if command -v findmnt >/dev/null 2>&1; then
            findmnt /app/config 2>/dev/null | while IFS= read -r line; do
                log_debug "  ${line}" "${component}"
            done
        fi

        return 0
    else
        log_error "Configuration setup failed" "${component}"
        return 1
    fi
}

#=============================================================================
# FILE OPERATIONS FUNCTIONS
#=============================================================================

# Enhanced file copying with batch processing and detailed reporting
# This function uses Bash 5.2's improved glob and array handling
copy_default_files() {
    local source_dir="$1"
    local dest_dir="$2"
    local file_type="$3"
    local component="FILES"

    log_info "Processing ${file_type,,} files from ${source_dir}" "${component}"

    # Validate source directory
    if [[ ! -d "${source_dir}" ]]; then
        log_warning "Source directory does not exist: ${source_dir}" "${component}"
        return 0
    fi

    # Ensure destination directory exists
    if ! mkdir -p "${dest_dir}"; then
        log_error "Failed to create destination directory: ${dest_dir}" "${component}"
        return 1
    fi

    # Use Bash 5.2's improved find with process substitution for better performance
    local files_found=()
    local files_processed=0
    local files_copied=0
    local files_skipped=0
    local files_failed=0

    # Build file list using mapfile for efficiency (Bash 5.2 feature)
    mapfile -t files_found < <(find "${source_dir}" -maxdepth 1 -type f -print 2>/dev/null || true)

    # Process files if any were found
    if [[ ${#files_found[@]} -eq 0 ]]; then
        log_info "No ${file_type,,} files found in ${source_dir}" "${component}"
        return 0
    fi

    # Process each file with detailed logging
    for source_file in "${files_found[@]}"; do
        [[ -f "${source_file}" ]] || continue

        local filename
        filename=$(basename "${source_file}")
        local dest_file="${dest_dir}/${filename}"

        ((files_processed++))

        # Check if destination file already exists
        if [[ -f "${dest_file}" ]]; then
            log_debug "${file_type} ${filename} already exists, skipping" "${component}"
            ((files_skipped++))
            continue
        fi

        # Copy file with error handling
        if cp "${source_file}" "${dest_file}" 2>/dev/null; then
            log_success "${file_type} ${filename} copied successfully" "${component}"
            ((files_copied++))

            # Set proper ownership on copied files
            if [[ -n "${PUID:-}" && -n "${PGID:-}" ]]; then
                safe_chown "${dest_file}" "${PUID}:${PGID}" "${component}"
            fi

            # Set proper permissions based on file type
            if [[ "${file_type}" == "Script" ]]; then
                safe_chmod "${dest_file}" "755" "${component}"
            else
                safe_chmod "${dest_file}" "644" "${component}"
            fi
        else
            log_error "Failed to copy ${file_type,,} ${filename}" "${component}"
            ((files_failed++))
        fi
    done

    # Summary reporting
    log_info "${file_type} processing complete: ${files_processed} found, ${files_copied} copied, ${files_skipped} skipped, ${files_failed} failed" "${component}"

    # Return success if no failures occurred
    return $((files_failed > 0 ? 1 : 0))
}

# Process all default files with parallel execution for performance
process_default_files() {
    local component="FILES"
    log_info "Processing all default files" "${component}"

    # Define file processing tasks
    local -A file_tasks=(
        ["${DEFAULTS_DIR}/templates:${TEMPLATES_DIR}:Template"]="Template files"
        ["${DEFAULTS_DIR}/scripts:${SCRIPTS_DIR}:Script"]="Script files"
    )

    local success_count=0
    local total_tasks=${#file_tasks[@]}

    # Process each file type
    for task_spec in "${!file_tasks[@]}"; do
        # Parse task specification
        IFS=':' read -r source_dir dest_dir file_type <<< "${task_spec}"
        local description="${file_tasks[${task_spec}]}"

        log_debug "Processing ${description}: ${source_dir} -> ${dest_dir}" "${component}"

        if measure_execution_time copy_default_files "${source_dir}" "${dest_dir}" "${file_type}"; then
            ((success_count++))
        fi
    done

    # Report overall results
    if (( success_count == total_tasks )); then
        log_success "All default files processed successfully (${success_count}/${total_tasks})" "${component}"
        return 0
    else
        log_error "Some file processing tasks failed (${success_count}/${total_tasks})" "${component}"
        return 1
    fi
}

#=============================================================================
# PERMISSION MANAGEMENT FUNCTIONS
#=============================================================================

# Advanced permission management with ownership and access control
manage_permissions() {
    local component="PERMS"
    log_info "Managing file permissions and ownership" "${component}"

    # Get user and group IDs from environment
    local puid="${PUID:-}"
    local pgid="${PGID:-}"

    # Define directories that need permission management
    local -a managed_dirs=(
        "${CONFIG_DIR}"
        "${TEMPLATES_DIR}"
        "${DATA_DIR}"
        "${LOGS_DIR}"
        "${SCRIPTS_DIR}"
    )

    # Set ownership if PUID and PGID are provided
    if [[ -n "${puid}" && -n "${pgid}" ]]; then
        log_info "Setting ownership to ${puid}:${pgid} for managed directories" "${component}"

        local ownership_success=0
        local ownership_total=${#managed_dirs[@]}

        for dir_path in "${managed_dirs[@]}"; do
            if [[ -d "${dir_path}" ]]; then
                if safe_chown "${dir_path}" "${puid}:${pgid}" "${component}"; then
                    ((ownership_success++))
                fi
            fi
        done

        log_info "Ownership update complete: ${ownership_success}/${ownership_total} directories" "${component}"
    else
        log_debug "PUID/PGID not specified, skipping ownership changes" "${component}"
    fi

    # Set standard permissions for all managed directories
    log_debug "Setting standard permissions (755) for managed directories" "${component}"

    local permission_success=0
    local permission_total=${#managed_dirs[@]}

    for dir_path in "${managed_dirs[@]}"; do
        if [[ -d "${dir_path}" ]]; then
            if safe_chmod "${dir_path}" "755" "${component}"; then
                ((permission_success++))
            fi
        fi
    done

    # Make scripts executable using find with improved performance
    if [[ -d "${SCRIPTS_DIR}" ]]; then
        log_debug "Making scripts executable in ${SCRIPTS_DIR}" "${component}"

        # Use Bash 5.2's improved find command handling
        local script_count=0
        while IFS= read -r -d '' script_file; do
            if safe_chmod "${script_file}" "+x" "${component}"; then
                ((script_count++))
            fi
        done < <(find "${SCRIPTS_DIR}" -type f \( -name "*.sh" -o -name "*.py" \) -print0 2>/dev/null || true)

        if (( script_count > 0 )); then
            log_success "Made ${script_count} script files executable" "${component}"
        fi
    fi

    # Report overall permission management results
    log_info "Permission management complete: ${permission_success}/${permission_total} directories processed" "${component}"

    return $((permission_success == permission_total ? 0 : 1))
}

#=============================================================================
# DIAGNOSTIC AND MONITORING FUNCTIONS
#=============================================================================

# System diagnostics and environment validation
run_system_diagnostics() {
    local component="DIAG"
    log_info "Running system diagnostics" "${component}"

    # Check available disk space
    local disk_usage
    disk_usage=$(df -h "${APP_ROOT}" 2>/dev/null | awk 'NR==2 {print $5}' | tr -d '%')

    if [[ -n "${disk_usage}" ]]; then
        if (( disk_usage > 90 )); then
            log_warning "Disk usage is high: ${disk_usage}%" "${component}"
        else
            log_debug "Disk usage: ${disk_usage}%" "${component}"
        fi
    fi

    # Check memory usage
    local memory_info
    if memory_info=$(free -m 2>/dev/null); then
        local available_memory
        available_memory=$(echo "${memory_info}" | awk '/^Mem:/ {print $7}')
        log_debug "Available memory: ${available_memory:-unknown}MB" "${component}"
    fi

    # Validate required commands
    local required_commands=("jq" "find" "mv" "cp" "chmod" "chown")
    local missing_commands=()

    for cmd in "${required_commands[@]}"; do
        if ! command -v "${cmd}" >/dev/null 2>&1; then
            missing_commands+=("${cmd}")
        fi
    done

    if [[ ${#missing_commands[@]} -gt 0 ]]; then
        log_error "Missing required commands: ${missing_commands[*]}" "${component}"
        return 1
    else
        log_success "All required commands available" "${component}"
    fi

    # Check environment variables
    local required_env_vars=("JELLYFIN_SERVER_URL" "JELLYFIN_API_KEY" "DISCORD_WEBHOOK_URL")
    local missing_env_vars=()

    for env_var in "${required_env_vars[@]}"; do
        if [[ -z "${!env_var:-}" ]]; then
            missing_env_vars+=("${env_var}")
        fi
    done

    if [[ ${#missing_env_vars[@]} -gt 0 ]]; then
        log_warning "Missing environment variables: ${missing_env_vars[*]}" "${component}"
    else
        log_success "All required environment variables present" "${component}"
    fi

    return 0
}

# Performance monitoring and resource usage reporting
monitor_performance() {
    local component="PERF"
    local current_time
    current_time=$(date +%s.%N)

    # Calculate elapsed time
    local elapsed_time
    elapsed_time=$(awk "BEGIN {printf \"%.3f\", ${current_time} - ${START_TIME}}")

    # Get process information
    local process_info
    if process_info=$(ps -o pid,ppid,pgid,vsz,rss,pcpu,pmem,time,cmd -p $$ 2>/dev/null); then
        log_debug "Process info for PID $$: ${process_info}" "${component}"
    fi

    # Report resource usage
    log_debug "Elapsed time: ${elapsed_time}s" "${component}"

    return 0
}

#=============================================================================
# MAIN EXECUTION LOGIC
#=============================================================================

# Main initialization function that orchestrates the entire setup process
main() {
    local component="MAIN"

    # Initialize logging as the first step

    # Initialize debug logging if needed
    init_debug_logging

    # Register cleanup handlers
    register_cleanup

    log_info "Starting JellyNotify Docker container initialization v${SCRIPT_VERSION}" "${component}"
    log_info "Process ID: ${SCRIPT_PID}" "${component}"
    log_info "Bash version: ${BASH_VERSION}" "${component}"

    # Run system diagnostics first
    if ! run_system_diagnostics; then
        log_critical "System diagnostics failed, aborting initialization" "${component}"
        exit 1
    fi

    # Create required directory structure
    if ! measure_execution_time create_required_directories; then
        log_critical "Failed to create required directories" "${component}"
        exit 1
    fi

    # Setup configuration files
    if ! measure_execution_time setup_configuration; then
        log_critical "Configuration setup failed" "${component}"
        exit 1
    fi

    # Process default files (templates, scripts, etc.)
    if ! measure_execution_time process_default_files; then
        log_critical "Default file processing failed" "${component}"
        exit 1
    fi

    # Manage permissions and ownership
    if ! measure_execution_time manage_permissions; then
        log_warning "Permission management had issues, but continuing" "${component}"
    fi

    # Setup application user for running Python script
    if [[ -n "${PUID:-}" && -n "${PGID:-}" ]]; then
        if ! measure_execution_time setup_application_user; then
            log_warning "User setup failed, will run as root" "${component}"
        fi
    else
        log_info "PUID/PGID not specified, application will run as root" "${component}"
    fi

    # Final performance monitoring
    monitor_performance

    # Final validation
    if [[ -f "${CONFIG_FILE}" ]] && validate_json_file "${CONFIG_FILE}" "${component}"; then
        log_success "Configuration validation successful" "${component}"
    else
        log_error "Final configuration validation failed" "${component}"
        exit 1
    fi

    log_success "JellyNotify container initialization completed successfully" "${component}"
    log_info "Starting application..." "${component}"

    # Execute the main command passed to the script with proper user context
    # This preserves all original arguments and handles them properly
    if [[ -n "${APP_USER:-}" ]]; then
        exec_as_app_user "$@"
    else
        exec "$@"
    fi
}

#=============================================================================
# SCRIPT EXECUTION
#=============================================================================

# Execute main function with all script arguments
# Using Bash 5.2's enhanced argument handling
main "$@"