#!/usr/bin/env python3
# scripts/test_webhook.py - Test webhook endpoint

import json
import requests
import sys
from datetime import datetime

def test_webhook():
    """Test the webhook endpoint with sample data"""
    
    webhook_url = "http://localhost:8080/webhook"
    
    # Sample webhook payload (as it would come from Jellyfin)
    test_payload = {
        "ItemId": "test-item-12345",
        "Name": "The Matrix",
        "ItemType": "Movie",
        "Year": 1999,
        "Overview": "A computer programmer is led to fight an underground war against powerful computers who have constructed his entire reality with a system called the Matrix.",
        "Video_0_Height": 1080,
        "Video_0_Width": 1920,
        "Video_0_Codec": "h264",
        "Video_0_Profile": "High",
        "Video_0_VideoRange": "SDR",
        "Video_0_FrameRate": 23.976,
        "Video_0_AspectRatio": "16:9",
        "Audio_0_Codec": "ac3",
        "Audio_0_Channels": 6,
        "Audio_0_Language": "eng",
        "Audio_0_Bitrate": 448000,
        "Provider_imdb": "tt0133093",
        "Provider_tmdb": "603"
    }
    
    print("🧪 Testing webhook endpoint...")
    print(f"📡 Sending to: {webhook_url}")
    print(f"📦 Payload: {json.dumps(test_payload, indent=2)}")
    
    try:
        response = requests.post(webhook_url, json=test_payload, timeout=30)
        
        if response.status_code == 200:
            print("✅ Webhook test successful!")
            print(f"📄 Response: {response.json()}")
        else:
            print(f"❌ Webhook test failed with status {response.status_code}")
            print(f"📄 Response: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to webhook service. Is it running?")
        print("💡 Try: docker-compose up -d")
    except requests.exceptions.Timeout:
        print("❌ Webhook request timed out")
    except Exception as e:
        print(f"❌ Error testing webhook: {e}")

def test_upgrade_scenario():
    """Test upgrade detection with two similar items"""
    
    webhook_url = "http://localhost:8080/webhook"
    
    # First item (original quality)
    original_payload = {
        "ItemId": "upgrade-test-item",
        "Name": "Blade Runner 2049",
        "ItemType": "Movie",
        "Year": 2017,
        "Video_0_Height": 1080,
        "Video_0_Codec": "h264",
        "Audio_0_Codec": "ac3",
        "Audio_0_Channels": 6
    }
    
    # Second item (upgraded quality)
    upgraded_payload = {
        "ItemId": "upgrade-test-item",  # Same ID
        "Name": "Blade Runner 2049",
        "ItemType": "Movie",
        "Year": 2017,
        "Video_0_Height": 2160,  # 4K upgrade
        "Video_0_Codec": "hevc",  # Better codec
        "Video_0_VideoRange": "HDR",  # HDR upgrade
        "Audio_0_Codec": "dts",  # Better audio
        "Audio_0_Channels": 8  # More channels
    }
    
    print("🧪 Testing upgrade detection...")
    
    # Send original item
    print("📡 Sending original item...")
    response1 = requests.post(webhook_url, json=original_payload)
    print(f"✅ Original response: {response1.status_code}")
    
    # Wait a moment
    import time
    time.sleep(2)
    
    # Send upgraded item
    print("📡 Sending upgraded item...")
    response2 = requests.post(webhook_url, json=upgraded_payload)
    print(f"✅ Upgrade response: {response2.status_code}")
    
    print("🎉 Upgrade test complete! Check Discord for two different notifications.")

def test_multi_webhook_scenario():
    """Test multi-webhook routing"""
    
    webhooks_url = "http://localhost:8080/webhooks"
    webhook_url = "http://localhost:8080/webhook"
    
    print("🧪 Testing multi-webhook routing...")
    
    # Check webhook configuration
    try:
        response = requests.get(webhooks_url)
        if response.status_code == 200:
            webhook_config = response.json()
            print(f"📄 Webhook config: {json.dumps(webhook_config, indent=2)}")
            
            routing_enabled = webhook_config.get('routing_enabled', False)
            if not routing_enabled:
                print("⚠️  Multi-webhook routing is disabled. Enable it in config.json")
                return
        else:
            print(f"❌ Could not get webhook config: {response.status_code}")
            return
    except Exception as e:
        print(f"❌ Error getting webhook config: {e}")
        return
    
    # Test movie payload
    movie_payload = {
        "ItemId": "test-movie-123",
        "Name": "The Matrix",
        "ItemType": "Movie",
        "Year": 1999,
        "Video_0_Height": 1080,
        "Video_0_Codec": "h264"
    }
    
    # Test TV show payload
    tv_payload = {
        "ItemId": "test-episode-456",
        "Name": "Pilot",
        "ItemType": "Episode",
        "SeriesName": "Breaking Bad",
        "SeasonNumber00": "01",
        "EpisodeNumber00": "01",
        "Video_0_Height": 1080,
        "Video_0_Codec": "h264"
    }
    
    # Send movie
    print("📡 Sending movie notification...")
    response1 = requests.post(webhook_url, json=movie_payload)
    print(f"✅ Movie response: {response1.status_code}")
    
    # Wait a moment
    import time
    time.sleep(2)
    
    # Send TV episode
    print("📡 Sending TV episode notification...")
    response2 = requests.post(webhook_url, json=tv_payload)
    print(f"✅ TV response: {response2.status_code}")
    
    print("🎉 Multi-webhook test complete! Check Discord channels for notifications.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "upgrade":
            test_upgrade_scenario()
        elif sys.argv[1] == "multi-webhook":
            test_multi_webhook_scenario()
        else:
            print("Usage: python test_webhook.py [upgrade|multi-webhook]")
    else:
        test_webhook()