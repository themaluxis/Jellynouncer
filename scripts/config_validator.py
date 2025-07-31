#!/usr/bin/env python3
# scripts/config_validator.py - Validate configuration

import json
import os
import sys
import requests
from urllib.parse import urlparse

def validate_config():
    """Validate the configuration file and environment variables"""
    
    errors = []
    warnings = []
    
    # Check environment variables
    required_env_vars = [
        'JELLYFIN_SERVER_URL',
        'JELLYFIN_API_KEY',
        'DISCORD_WEBHOOK_URL'
    ]
    
    for var in required_env_vars:
        if not os.getenv(var):
            errors.append(f"Missing required environment variable: {var}")
    
    # Validate Jellyfin URL
    jellyfin_url = os.getenv('JELLYFIN_SERVER_URL')
    if jellyfin_url:
        parsed = urlparse(jellyfin_url)
        if not parsed.scheme or not parsed.netloc:
            errors.append(f"Invalid Jellyfin URL format: {jellyfin_url}")
        
        # Test connectivity
        try:
            response = requests.get(f"{jellyfin_url}/health", timeout=5)
            if response.status_code != 200:
                warnings.append(f"Jellyfin server responded with status {response.status_code}")
        except:
            warnings.append("Could not connect to Jellyfin server")
    
    # Validate Discord webhook URL
    discord_url = os.getenv('DISCORD_WEBHOOK_URL')
    if discord_url:
        if not discord_url.startswith('https://discord.com/api/webhooks/'):
            errors.append("Discord webhook URL should start with 'https://discord.com/api/webhooks/'")
        
        # Test Discord webhook
        try:
            test_payload = {
                "embeds": [{
                    "title": "🧪 Configuration Test",
                    "description": "If you see this, your webhook is working!",
                    "color": 65280
                }]
            }
            response = requests.post(discord_url, json=test_payload, timeout=10)
            if response.status_code == 204:
                print("✅ Discord webhook test successful!")
            else:
                warnings.append(f"Discord webhook test failed with status {response.status_code}")
        except:
            warnings.append("Could not test Discord webhook")
    
    # Check config file
    config_path = "./config/config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                
            # Validate structure
            required_sections = ['jellyfin', 'discord', 'database', 'templates', 'notifications']
            for section in required_sections:
                if section not in config:
                    warnings.append(f"Missing config section: {section}")
                    
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON in config file: {e}")
    else:
        warnings.append("Config file not found, using defaults")
    
    # Check template files
    template_dir = "./templates"
    required_templates = ['new_item.j2', 'upgraded_item.j2']
    
    for template in required_templates:
        template_path = os.path.join(template_dir, template)
        if not os.path.exists(template_path):
            errors.append(f"Missing template file: {template_path}")
    
    # Check directory structure
    required_dirs = ['./data', './logs', './config', './templates']
    for dir_path in required_dirs:
        if not os.path.exists(dir_path):
            warnings.append(f"Directory does not exist: {dir_path}")
    
    # Report results
    print("🔍 Configuration Validation Results")
    print("=" * 50)
    
    if errors:
        print("\n❌ ERRORS:")
        for error in errors:
            print(f"  • {error}")
    
    if warnings:
        print("\n⚠️  WARNINGS:")
        for warning in warnings:
            print(f"  • {warning}")
    
    if not errors and not warnings:
        print("\n✅ Configuration is valid!")
    elif not errors:
        print(f"\n✅ Configuration is valid with {len(warnings)} warnings.")
    else:
        print(f"\n❌ Configuration has {len(errors)} errors and {len(warnings)} warnings.")
        return False
    
    return True

if __name__ == "__main__":
    success = validate_config()
    sys.exit(0 if success else 1)