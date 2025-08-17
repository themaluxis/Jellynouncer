#!/usr/bin/env python3
"""
Test script to validate the web API structure and identify issues
"""

import sys
import os
import importlib.util

# Add parent directory to path so we can import jellynouncer modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 60)
print("Jellynouncer Web API Test Suite")
print("=" * 60)

# Test 1: Check if required modules are importable
required_modules = [
    'fastapi',
    'uvicorn', 
    'pydantic',
    'aiosqlite',
    'jwt',
    'passlib',
    'bcrypt',
    'cryptography'
]

print("\n1. Checking required Python modules:")
missing_modules = []
for module in required_modules:
    spec = importlib.util.find_spec(module)
    if spec is None:
        print(f"   [X] {module} - NOT INSTALLED")
        missing_modules.append(module)
    else:
        print(f"   [OK] {module} - OK")

if missing_modules:
    print(f"\n[!]  Missing modules: {', '.join(missing_modules)}")
    print("   Please install with: pip install " + " ".join(missing_modules))
    print("\n   Or install all requirements with: pip install -r requirements.txt")
else:
    print("\n[OK] All required modules are installed")

# Test 2: Check core Jellynouncer modules
print("\n2. Checking Jellynouncer modules:")

jellynouncer_modules = [
    'jellynouncer.utils',
    'jellynouncer.config_models',
    'jellynouncer.database_manager',
    'jellynouncer.webhook_service',
    'jellynouncer.ssl_manager'
]

for module in jellynouncer_modules:
    try:
        spec = importlib.util.find_spec(module)
        if spec:
            print(f"   [OK] {module} - Found")
        else:
            print(f"   [X] {module} - Not found")
    except Exception as e:
        print(f"   [X] {module} - Error: {e}")

# Test 3: Check file structure
print("\n3. Checking file structure:")

import os
from pathlib import Path

required_files = [
    'jellynouncer/web_api.py',
    'jellynouncer/ssl_manager.py',
    'jellynouncer/utils.py',
    'config/config.json',
    'requirements.txt'
]

required_dirs = [
    'data',
    'logs',
    'templates',
    'web/src',
    'config'
]

for file in required_files:
    if Path(file).exists():
        print(f"   [OK] {file} - Exists")
    else:
        print(f"   [!]  {file} - Missing (will be created on first run)")

for dir in required_dirs:
    if Path(dir).exists():
        print(f"   [OK] {dir}/ - Exists")
    else:
        print(f"   [!]  {dir}/ - Missing (will be created on first run)")

# Test 4: Basic syntax validation
print("\n4. Validating Python syntax:")

files_to_check = [
    'jellynouncer/web_api.py',
    'jellynouncer/ssl_manager.py'
]

import ast

for file in files_to_check:
    if Path(file).exists():
        try:
            with open(file, 'r', encoding='utf-8') as f:
                source = f.read()
            ast.parse(source)
            print(f"   [OK] {file} - Syntax OK")
        except SyntaxError as e:
            print(f"   [X] {file} - Syntax error at line {e.lineno}: {e.msg}")
        except Exception as e:
            print(f"   [X] {file} - Error: {e}")

print("\n" + "=" * 60)

# Summary
if not missing_modules:
    print("[OK] Web API structure appears valid!")
    print("\nTo start the web server:")
    print("   python jellynouncer/web_api.py")
    print("\nOr run both services together:")
    print("   python main.py")
    print("\nThe server will run on:")
    print("   - HTTP: http://localhost:1985")
    print("   - HTTPS: https://localhost:9000 (if SSL is configured)")
else:
    print("[X] Please install missing dependencies first")
    print("   Run: pip install -r requirements.txt")

print("=" * 60)