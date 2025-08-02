#!/bin/bash

# Bash strict mode for better error handling
set -euo pipefail

# Enable debug mode if DEBUG environment variable is set
if [[ "${DEBUG:-}" == "true" ]]; then
    set -x
fi

# Function to log messages
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" >&2
}



# Function to safely update JSON using jq
update_config_json() {
    local config_file="$1"

    log "Updating configuration file: ${config_file}"
    log "Using jq for JSON manipulation"

    # Create a temporary file for atomic updates
    local temp_file
    temp_file=$(mktemp "${config_file}.XXXXXX")

    # Use jq to update the JSON file safely
    jq --arg jellyfin_url "${JELLYFIN_SERVER_URL:-null}" \
       --arg api_key "${JELLYFIN_API_KEY:-null}" \
       --arg user_id "${JELLYFIN_USER_ID:-null}" \
       --arg webhook_url "${DISCORD_WEBHOOK_URL:-null}" \
       --arg movies_url "${DISCORD_WEBHOOK_URL_MOVIES:-null}" \
       --arg tv_url "${DISCORD_WEBHOOK_URL_TV:-null}" \
       --arg music_url "${DISCORD_WEBHOOK_URL_MUSIC:-null}" \
       '
       # Update jellyfin configuration
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
       .discord.routing.enabled = (if ($movies_url != "null" or $tv_url != "null" or $music_url != "null") then true else .discord.routing.enabled end)
       ' "${config_file}" > "${temp_file}"

    # Validate the resulting JSON
    if jq empty "${temp_file}" 2>/dev/null; then
        # Atomically replace the original file
        mv "${temp_file}" "${config_file}"
        log "Successfully updated configuration using jq"
    else
        log "ERROR: Generated invalid JSON, keeping original file"
        rm -f "${temp_file}"
        return 1
    fi
}

# Function to copy and setup default files
copy_default_files() {
    local source_dir="$1"
    local dest_dir="$2"
    local file_type="$3"

    if [[ ! -d "${source_dir}" ]]; then
        log "WARNING: Source directory ${source_dir} does not exist"
        return 0
    fi

    # Create destination directory if it doesn't exist
    mkdir -p "${dest_dir}"

    # Copy files if they don't exist in destination
    local copied_count=0
    while IFS= read -r -d '' source_file; do
        local filename
        filename=$(basename "${source_file}")
        local dest_file="${dest_dir}/${filename}"

        if [[ ! -f "${dest_file}" ]]; then
            if cp "${source_file}" "${dest_file}"; then
                log "${file_type} ${filename} copied successfully"

                # Make scripts executable
                if [[ "${file_type}" == "Script" ]]; then
                    chmod +x "${dest_file}" || log "WARNING: Could not make ${dest_file} executable"
                fi

                ((copied_count++))
            else
                log "ERROR: Failed to copy ${source_file} to ${dest_file}"
            fi
        fi
    done < <(find "${source_dir}" -maxdepth 1 -type f -print0)

    if [[ ${copied_count} -gt 0 ]]; then
        log "Copied ${copied_count} ${file_type,,} files"
    fi
}

# Function to set file permissions safely
set_permissions() {
    local target="$1"
    local perm="$2"

    if [[ -e "${target}" ]]; then
        if chmod "${perm}" "${target}" 2>/dev/null; then
            log "Set permissions ${perm} on ${target}"
        else
            log "WARNING: Could not set permissions ${perm} on ${target}"
        fi
    fi
}

# Function to set ownership safely
set_ownership() {
    local target="$1"
    local owner="$2"

    if [[ -e "${target}" ]]; then
        if chown "${owner}" "${target}" 2>/dev/null; then
            log "Set ownership ${owner} on ${target}"
        else
            log "WARNING: Could not set ownership ${owner} on ${target}"
        fi
    fi
}

# Main execution starts here
main() {
    log "Starting JellyNotify Docker container initialization"

    # Create necessary directories if they don't exist
    local required_dirs=("/app/config" "/app/templates" "/app/data" "/app/logs" "/app/scripts")
    for dir in "${required_dirs[@]}"; do
        if mkdir -p "${dir}"; then
            log "Created directory: ${dir}"
        else
            log "ERROR: Failed to create directory: ${dir}"
            exit 1
        fi
    done

    # Copy default configuration file if it doesn't exist
    if [[ ! -f "/app/config/config.json" ]]; then
        if [[ -f "/app/defaults/config.json" ]]; then
            log "Config file not found, copying default configuration..."
            if cp "/app/defaults/config.json" "/app/config/config.json"; then
                log "Default config copied successfully"

                # Update the config file with environment variables
                update_config_json "/app/config/config.json"
            else
                log "ERROR: Failed to copy default config"
                exit 1
            fi
        else
            log "ERROR: Default config file not found at /app/defaults/config.json"
            exit 1
        fi
    else
        log "Config file exists, updating with environment variables..."
        update_config_json "/app/config/config.json"
    fi

    # Copy default templates
    copy_default_files "/app/defaults/templates" "/app/templates" "Template"

    # Copy default scripts
    copy_default_files "/app/defaults/scripts" "/app/scripts" "Script"

    # Set proper permissions for mounted volumes if PUID and PGID are provided
    if [[ -n "${PUID:-}" ]] && [[ -n "${PGID:-}" ]]; then
        log "🔐 Setting permissions for user ${PUID}:${PGID}"

        local dirs_to_chown=("/app/config" "/app/templates" "/app/data" "/app/logs" "/app/scripts")
        for dir in "${dirs_to_chown[@]}"; do
            set_ownership "${dir}" "${PUID}:${PGID}"
        done
    fi

    # Set proper file permissions
    local dirs_for_permissions=("/app/config" "/app/templates" "/app/data" "/app/logs" "/app/scripts")
    for dir in "${dirs_for_permissions[@]}"; do
        set_permissions "${dir}" "755"
    done

    # Make scripts executable
    if [[ -d "/app/scripts" ]]; then
        find "/app/scripts" -type f \( -name "*.sh" -o -name "*.py" \) -exec chmod +x {} \; 2>/dev/null || true
    fi

    log "✅ Configuration initialized successfully. Starting application..."

    # Execute the main command
    exec "$@"
}

# Call main function with all arguments
main "$@"