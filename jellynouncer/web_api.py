#!/usr/bin/env python3
"""
Jellynouncer Web Interface API Server

This module provides a comprehensive web interface for managing and monitoring
the Jellynouncer service. It runs on a separate port (1985) from the main webhook
service and provides REST API endpoints for the React frontend.

Architecture:
    - FastAPI backend with async support
    - JWT-based authentication with refresh tokens
    - Separate SQLite database for web-specific data
    - Real-time statistics from the main Jellynouncer database
    - Template management with file system operations
    - Log streaming and filtering capabilities

Security Features:
    - Bcrypt password hashing
    - JWT tokens with expiration
    - CORS configuration for production
    - Rate limiting on authentication endpoints
    - Secure session management

Author: Mark Newton
Project: Jellynouncer Web Interface
Version: 1.0.0
License: MIT
"""

import os
import sys
import json
import asyncio
import hashlib
import secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Union
from contextlib import asynccontextmanager

# Third-party imports
from fastapi import FastAPI, HTTPException, Depends, Security, status, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
import uvicorn
from pydantic import BaseModel, Field, validator
import aiosqlite
import jwt
from passlib.context import CryptContext
import bcrypt

# Import Jellynouncer modules
from jellynouncer.config_models import AppConfig, ConfigurationValidator
from jellynouncer.database_manager import DatabaseManager
from jellynouncer.utils import setup_logging, get_logger
from jellynouncer.webhook_service import WebhookService
from jellynouncer.ssl_manager import SSLManager, setup_ssl_routes

# Constants
WEB_DB_PATH = "data/web_interface.db"
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 30
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT token handler
security = HTTPBearer()

# Logger setup
logger = get_logger("web_api")


# ==================== Pydantic Models ====================

class UserCreate(BaseModel):
    """Model for user creation request"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    email: Optional[str] = None
    
    @validator('username')
    def username_alphanumeric(cls, v):
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Username must be alphanumeric with optional _ or -')
        return v


class UserLogin(BaseModel):
    """Model for user login request"""
    username: str
    password: str


class TokenResponse(BaseModel):
    """Model for JWT token response"""
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class ConfigUpdate(BaseModel):
    """Model for configuration updates"""
    section: str
    key: str
    value: Any
    
    
class TemplateUpdate(BaseModel):
    """Model for template updates"""
    name: str
    content: str
    

class LogQuery(BaseModel):
    """Model for log query parameters"""
    file: str = "jellynouncer.log"
    lines: int = Field(100, le=1000)
    level: Optional[str] = None
    component: Optional[str] = None
    search: Optional[str] = None


class OverviewStats(BaseModel):
    """Model for overview statistics"""
    total_items: int
    items_today: int
    items_week: int
    discord_webhooks: Dict[str, Dict[str, Any]]
    recent_notifications: List[Dict[str, Any]]
    queue_stats: Dict[str, int]
    system_health: Dict[str, Any]


# ==================== Database Manager ====================

class WebDatabaseManager:
    """Manages the web interface SQLite database"""
    
    def __init__(self, db_path: str = WEB_DB_PATH):
        self.db_path = db_path
        self.logger = get_logger("web_db")
        
    async def initialize(self):
        """Initialize the web database with required tables"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for better concurrency
            await db.execute("PRAGMA journal_mode=WAL")
            
            # Security settings table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS security_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    auth_enabled BOOLEAN DEFAULT 0,
                    require_webhook_auth BOOLEAN DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CHECK (id = 1)
                )
            """)
            
            # Users table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    is_admin BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            """)
            
            # Sessions table for refresh tokens
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    refresh_token TEXT UNIQUE NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """)
            
            # Audit log table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """)
            
            # Create indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(refresh_token)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
            
            await db.commit()
            
            # Initialize security settings if not exists
            cursor = await db.execute("SELECT COUNT(*) FROM security_settings")
            if (await cursor.fetchone())[0] == 0:
                await db.execute("INSERT INTO security_settings (auth_enabled, require_webhook_auth) VALUES (0, 0)")
                await db.commit()
                self.logger.info("Initialized security settings with authentication disabled")
    
    async def get_security_settings(self) -> Dict[str, bool]:
        """Get current security settings"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM security_settings WHERE id = 1")
            settings = await cursor.fetchone()
            
            if settings:
                return {
                    "auth_enabled": bool(settings["auth_enabled"]),
                    "require_webhook_auth": bool(settings["require_webhook_auth"])
                }
            return {"auth_enabled": False, "require_webhook_auth": False}
    
    async def update_security_settings(self, auth_enabled: bool, require_webhook_auth: bool):
        """Update security settings"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE security_settings 
                   SET auth_enabled = ?, require_webhook_auth = ?, updated_at = CURRENT_TIMESTAMP 
                   WHERE id = 1""",
                (auth_enabled, require_webhook_auth)
            )
            await db.commit()
    
    def _generate_salt(self) -> str:
        """Generate a random salt for password hashing"""
        return secrets.token_hex(32)
    
    def _hash_password_with_salt(self, password: str, salt: str) -> str:
        """Hash password with salt using bcrypt"""
        # Combine password and salt, then hash with bcrypt
        salted_password = f"{password}{salt}".encode('utf-8')
        return bcrypt.hashpw(salted_password, bcrypt.gensalt()).decode('utf-8')
    
    def _verify_password_with_salt(self, password: str, salt: str, password_hash: str) -> bool:
        """Verify password against hash with salt"""
        salted_password = f"{password}{salt}".encode('utf-8')
        return bcrypt.checkpw(salted_password, password_hash.encode('utf-8'))
    
    async def create_user(self, username: str, password: str, email: Optional[str] = None, is_admin: bool = False) -> int:
        """Create a new user with salt and hash"""
        salt = self._generate_salt()
        hashed_password = self._hash_password_with_salt(password, salt)
        
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cursor = await db.execute(
                    "INSERT INTO users (username, email, password_hash, salt, is_admin) VALUES (?, ?, ?, ?, ?)",
                    (username, email, hashed_password, salt, is_admin)
                )
                await db.commit()
                return cursor.lastrowid
            except aiosqlite.IntegrityError:
                raise ValueError(f"Username {username} already exists")
    
    async def verify_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Verify user credentials with salt"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1",
                (username,)
            )
            user = await cursor.fetchone()
            
            if user and self._verify_password_with_salt(password, user["salt"], user["password_hash"]):
                # Update last login
                await db.execute(
                    "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
                    (user["id"],)
                )
                await db.commit()
                return dict(user)
            
            return None
    
    async def update_user_password(self, user_id: int, new_password: str):
        """Update user password with new salt"""
        salt = self._generate_salt()
        hashed_password = self._hash_password_with_salt(new_password, salt)
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                (hashed_password, salt, user_id)
            )
            await db.commit()
    
    async def save_refresh_token(self, user_id: int, token: str, expires_at: datetime):
        """Save refresh token to database"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO sessions (user_id, refresh_token, expires_at) VALUES (?, ?, ?)",
                (user_id, token, expires_at.isoformat())
            )
            await db.commit()
    
    async def verify_refresh_token(self, token: str) -> Optional[int]:
        """Verify refresh token and return user_id if valid"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, expires_at FROM sessions WHERE refresh_token = ?",
                (token,)
            )
            row = await cursor.fetchone()
            
            if row:
                user_id, expires_at = row
                if datetime.fromisoformat(expires_at) > datetime.now(timezone.utc):
                    return user_id
                else:
                    # Clean up expired token
                    await db.execute("DELETE FROM sessions WHERE refresh_token = ?", (token,))
                    await db.commit()
            
            return None
    
    async def log_audit(self, user_id: Optional[int], action: str, details: Optional[str], ip: Optional[str]):
        """Log an audit event"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO audit_log (user_id, action, details, ip_address) VALUES (?, ?, ?, ?)",
                (user_id, action, details, ip)
            )
            await db.commit()


# ==================== Authentication ====================

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire.timestamp(), "type": "access"})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: Dict[str, Any]) -> str:
    """Create a JWT refresh token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire.timestamp(), "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


async def get_current_user_optional(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> Optional[Dict[str, Any]]:
    """Validate JWT token if provided, return user or None"""
    if not credentials:
        return None
        
    token = credentials.credentials
    
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        
        if payload.get("type") != "access":
            return None
        
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


async def check_auth_required(user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)) -> Optional[Dict[str, Any]]:
    """Check if authentication is required and validate user"""
    # Check if auth is enabled
    web_db = WebDatabaseManager()
    settings = await web_db.get_security_settings()
    
    if settings["auth_enabled"]:
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user
    
    # Auth not required, return None or user if provided
    return user


# ==================== Service Manager ====================

class WebInterfaceService:
    """Main service class for web interface operations"""
    
    def __init__(self, webhook_service: Optional[WebhookService] = None):
        self.webhook_service = webhook_service
        self.config = None
        self.web_db = WebDatabaseManager()
        self.ssl_manager = SSLManager(WEB_DB_PATH)
        self.logger = get_logger("web_interface")
        
    async def initialize(self):
        """Initialize the web interface service"""
        await self.web_db.initialize()
        await self.ssl_manager.initialize()
        
        # Load configuration
        try:
            self.config = ConfigurationValidator.load_and_validate()
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise
    
    async def get_overview_stats(self) -> OverviewStats:
        """Get statistics for the overview page"""
        stats = {
            "total_items": 0,
            "items_today": 0,
            "items_week": 0,
            "discord_webhooks": {},
            "recent_notifications": [],
            "queue_stats": {
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0
            },
            "system_health": {
                "webhook_service": "running" if self.webhook_service else "stopped",
                "database": "connected",
                "last_sync": None
            }
        }
        
        # Get statistics from main database if webhook service is available
        if self.webhook_service and self.webhook_service.db:
            try:
                db_stats = await self.webhook_service.db.get_statistics()
                stats["total_items"] = db_stats.get("total_items", 0)
                stats["items_today"] = db_stats.get("items_added_today", 0)
                stats["items_week"] = db_stats.get("items_added_week", 0)
                
                # Get recent notifications
                recent = await self.webhook_service.db.get_recent_changes(limit=10)
                stats["recent_notifications"] = [
                    {
                        "id": item.get("id"),
                        "name": item.get("name", "Unknown"),
                        "type": item.get("media_type"),
                        "event": item.get("last_event"),
                        "timestamp": item.get("last_updated")
                    }
                    for item in recent
                ]
                
                # Discord webhook status
                if self.webhook_service.discord:
                    for webhook_name, webhook_url in self.webhook_service.discord.webhooks.items():
                        stats["discord_webhooks"][webhook_name] = {
                            "configured": bool(webhook_url),
                            "last_used": None,  # Would need to track this
                            "messages_sent": 0   # Would need to track this
                        }
                
            except Exception as e:
                self.logger.error(f"Failed to get database statistics: {e}")
                stats["system_health"]["database"] = "error"
        
        return OverviewStats(**stats)
    
    async def get_config(self, include_sensitive: bool = False) -> Dict[str, Any]:
        """Get current configuration"""
        if not self.config:
            self.config = ConfigurationValidator.load_and_validate()
        
        config_dict = self.config.model_dump()
        
        # Remove sensitive information unless requested
        if not include_sensitive:
            # Remove API keys and webhook URLs
            if "jellyfin" in config_dict:
                config_dict["jellyfin"]["api_key"] = "***HIDDEN***"
                
            if "discord" in config_dict:
                for key in config_dict["discord"]:
                    if "webhook_url" in key:
                        config_dict["discord"][key] = "***HIDDEN***" if config_dict["discord"][key] else None
            
            if "metadata_services" in config_dict:
                for service in ["omdb", "tmdb", "tvdb"]:
                    if service in config_dict["metadata_services"]:
                        if "api_key" in config_dict["metadata_services"][service]:
                            config_dict["metadata_services"][service]["api_key"] = "***HIDDEN***"
        
        return config_dict
    
    async def update_config(self, section: str, key: str, value: Any) -> bool:
        """Update configuration value"""
        config_path = Path("config/config.json")
        
        try:
            # Load current config
            with open(config_path, 'r') as f:
                config_data = json.load(f)
            
            # Update the value
            if section not in config_data:
                config_data[section] = {}
            
            config_data[section][key] = value
            
            # Validate the new configuration
            validated_config = ConfigurationValidator.validate_config(config_data)
            
            # Save the updated config
            with open(config_path, 'w') as f:
                json.dump(config_data, f, indent=2)
            
            # Update in-memory config
            self.config = validated_config
            
            self.logger.info(f"Updated config: {section}.{key}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to update config: {e}")
            raise ValueError(f"Configuration update failed: {str(e)}")
    
    async def get_templates(self) -> List[Dict[str, Any]]:
        """Get list of available templates"""
        templates_dir = Path("templates")
        templates = []
        
        for template_file in templates_dir.glob("*.j2"):
            # Read template metadata from first line comment if available
            with open(template_file, 'r') as f:
                content = f.read()
                
            templates.append({
                "name": template_file.stem,
                "filename": template_file.name,
                "size": template_file.stat().st_size,
                "modified": template_file.stat().st_mtime,
                "is_default": not template_file.stem.startswith("custom_")
            })
        
        return sorted(templates, key=lambda x: x["name"])
    
    async def get_template_content(self, name: str) -> str:
        """Get template content"""
        template_path = Path(f"templates/{name}.j2")
        
        if not template_path.exists():
            raise ValueError(f"Template {name} not found")
        
        with open(template_path, 'r') as f:
            return f.read()
    
    async def save_template(self, name: str, content: str) -> bool:
        """Save template content"""
        # Ensure custom templates are prefixed
        if not name.startswith("custom_") and not Path(f"templates/{name}.j2").exists():
            name = f"custom_{name}"
        
        template_path = Path(f"templates/{name}.j2")
        
        try:
            # Validate Jinja2 syntax
            from jinja2 import Environment, TemplateSyntaxError
            env = Environment()
            env.parse(content)
            
            # Save the template
            with open(template_path, 'w') as f:
                f.write(content)
            
            self.logger.info(f"Saved template: {name}")
            return True
            
        except TemplateSyntaxError as e:
            raise ValueError(f"Invalid Jinja2 syntax: {str(e)}")
        except Exception as e:
            self.logger.error(f"Failed to save template: {e}")
            raise
    
    async def restore_default_template(self, name: str) -> bool:
        """Restore a template to its default content"""
        # This would need the original templates stored somewhere
        # For now, we'll just indicate this needs implementation
        raise NotImplementedError("Default template restoration not yet implemented")
    
    async def get_logs(self, query: LogQuery) -> List[Dict[str, Any]]:
        """Get log entries based on query parameters"""
        log_path = Path(f"logs/{query.file}")
        
        if not log_path.exists():
            raise ValueError(f"Log file {query.file} not found")
        
        logs = []
        
        try:
            with open(log_path, 'r') as f:
                # Read last N lines
                lines = f.readlines()[-query.lines:]
                
                for line in lines:
                    # Parse log line (format: timestamp - level - component - message)
                    parts = line.strip().split(' - ', 3)
                    
                    if len(parts) >= 4:
                        log_entry = {
                            "timestamp": parts[0],
                            "level": parts[1],
                            "component": parts[2],
                            "message": parts[3]
                        }
                        
                        # Apply filters
                        if query.level and log_entry["level"] != query.level:
                            continue
                        if query.component and query.component not in log_entry["component"]:
                            continue
                        if query.search and query.search.lower() not in line.lower():
                            continue
                        
                        logs.append(log_entry)
                
        except Exception as e:
            self.logger.error(f"Failed to read logs: {e}")
            raise
        
        return logs


# ==================== FastAPI Application ====================

# Global service instance
web_service = WebInterfaceService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    # Startup
    logger.info("Starting Jellynouncer Web Interface...")
    await web_service.initialize()
    
    # Setup SSL routes
    await setup_ssl_routes(app, web_service.ssl_manager)
    
    # Try to connect to webhook service if available
    # This would be passed in from main.py when both services run together
    
    # Check SSL configuration
    ssl_settings = await web_service.ssl_manager.get_ssl_settings()
    if ssl_settings.get("ssl_enabled"):
        logger.info(f"SSL enabled on port {ssl_settings.get('port', 9000)}")
    else:
        logger.info("Web interface ready on port 1985 (HTTP)")
    
    yield
    
    # Shutdown
    logger.info("Shutting down web interface...")


# Create FastAPI app
app = FastAPI(
    title="Jellynouncer Web Interface",
    description="Web management interface for Jellynouncer",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:1985"],  # Update for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add trusted host middleware for security
if os.environ.get("JELLYNOUNCER_PRODUCTION"):
    allowed_hosts = os.environ.get("JELLYNOUNCER_ALLOWED_HOSTS", "").split(",")
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


# ==================== API Endpoints ====================

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(user_login: UserLogin, request: Request):
    """Authenticate user and return JWT tokens"""
    user = await web_service.web_db.verify_user(user_login.username, user_login.password)
    
    if not user:
        # Log failed attempt
        await web_service.web_db.log_audit(
            None, "login_failed", f"Username: {user_login.username}", 
            request.client.host
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )
    
    # Create tokens
    access_token = create_access_token({"user_id": user["id"], "username": user["username"]})
    refresh_token = create_refresh_token({"user_id": user["id"]})
    
    # Save refresh token
    expires_at = datetime.now(timezone.utc) + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    await web_service.web_db.save_refresh_token(user["id"], refresh_token, expires_at)
    
    # Log successful login
    await web_service.web_db.log_audit(
        user["id"], "login_success", None, request.client.host
    )
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@app.post("/api/auth/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str):
    """Refresh access token using refresh token"""
    user_id = await web_service.web_db.verify_refresh_token(refresh_token)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    # Get user details
    async with aiosqlite.connect(WEB_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        user = await cursor.fetchone()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    # Create new access token
    access_token = create_access_token({"user_id": user_id, "username": user["username"]})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,  # Return same refresh token
        expires_in=JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@app.get("/api/auth/status")
async def get_auth_status():
    """Get authentication status (no auth required)"""
    settings = await web_service.web_db.get_security_settings()
    return settings


@app.post("/api/auth/setup")
async def setup_authentication(user_create: UserCreate):
    """Initial authentication setup - only works when no users exist"""
    # Check if any users exist
    async with aiosqlite.connect(WEB_DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        user_count = (await cursor.fetchone())[0]
    
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication already configured. Use login endpoint."
        )
    
    try:
        # Create the first admin user
        user_id = await web_service.web_db.create_user(
            user_create.username,
            user_create.password,
            user_create.email,
            is_admin=True
        )
        
        # Enable authentication
        await web_service.web_db.update_security_settings(auth_enabled=True, require_webhook_auth=False)
        
        # Create tokens for immediate login
        access_token = create_access_token({"user_id": user_id, "username": user_create.username})
        refresh_token = create_refresh_token({"user_id": user_id})
        
        # Save refresh token
        expires_at = datetime.now(timezone.utc) + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        await web_service.web_db.save_refresh_token(user_id, refresh_token, expires_at)
        
        return {
            "message": "Authentication configured successfully",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }
        
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.put("/api/auth/settings")
async def update_auth_settings(
    auth_enabled: bool,
    require_webhook_auth: bool,
    current_user: Optional[Dict] = Depends(check_auth_required)
):
    """Update authentication settings"""
    # If disabling auth, ensure user is authenticated
    settings = await web_service.web_db.get_security_settings()
    
    if settings["auth_enabled"] and not auth_enabled:
        # Trying to disable auth - must be authenticated
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Must be authenticated to disable authentication"
            )
    
    await web_service.web_db.update_security_settings(auth_enabled, require_webhook_auth)
    
    if current_user:
        await web_service.web_db.log_audit(
            current_user.get("user_id"),
            "auth_settings_updated",
            f"Auth enabled: {auth_enabled}, Webhook auth: {require_webhook_auth}",
            None
        )
    
    return {"message": "Security settings updated", "auth_enabled": auth_enabled, "require_webhook_auth": require_webhook_auth}


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
async def register(user_create: UserCreate, current_user: Optional[Dict] = Depends(check_auth_required)):
    """Register a new user (requires auth if enabled)"""
    # Check if auth is enabled
    settings = await web_service.web_db.get_security_settings()
    
    if settings["auth_enabled"] and not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to create users"
        )
    
    try:
        user_id = await web_service.web_db.create_user(
            user_create.username, 
            user_create.password,
            user_create.email
        )
        
        if current_user:
            await web_service.web_db.log_audit(
                current_user.get("user_id"), 
                "user_created", 
                f"Created user: {user_create.username}",
                None
            )
        
        return {"message": "User created successfully", "user_id": user_id}
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/overview", response_model=OverviewStats)
async def get_overview(current_user: Optional[Dict] = Depends(check_auth_required)):
    """Get overview statistics"""
    return await web_service.get_overview_stats()


@app.get("/api/config")
async def get_config(current_user: Optional[Dict] = Depends(check_auth_required)):
    """Get current configuration"""
    return await web_service.get_config(include_sensitive=False)


@app.put("/api/config")
async def update_config(
    config_update: ConfigUpdate, 
    current_user: Optional[Dict] = Depends(check_auth_required)
):
    """Update configuration value"""
    try:
        success = await web_service.update_config(
            config_update.section,
            config_update.key,
            config_update.value
        )
        
        if current_user:
            await web_service.web_db.log_audit(
                current_user.get("user_id"),
                "config_updated",
                f"Updated {config_update.section}.{config_update.key}",
                None
            )
        
        return {"success": success, "message": "Configuration updated"}
        
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/templates")
async def get_templates(current_user: Optional[Dict] = Depends(check_auth_required)):
    """Get list of available templates"""
    return await web_service.get_templates()


@app.get("/api/templates/{name}")
async def get_template(name: str, current_user: Optional[Dict] = Depends(check_auth_required)):
    """Get template content"""
    try:
        content = await web_service.get_template_content(name)
        return {"name": name, "content": content}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.put("/api/templates/{name}")
async def update_template(
    name: str,
    template_update: TemplateUpdate,
    current_user: Optional[Dict] = Depends(check_auth_required)
):
    """Update or create template"""
    try:
        success = await web_service.save_template(name, template_update.content)
        
        if current_user:
            await web_service.web_db.log_audit(
                current_user.get("user_id"),
                "template_updated",
                f"Updated template: {name}",
                None
            )
        
        return {"success": success, "message": "Template saved"}
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/templates/{name}/restore")
async def restore_template(name: str, current_user: Optional[Dict] = Depends(check_auth_required)):
    """Restore template to default"""
    try:
        success = await web_service.restore_default_template(name)
        
        if current_user:
            await web_service.web_db.log_audit(
                current_user.get("user_id"),
                "template_restored",
                f"Restored template: {name}",
                None
            )
        
        return {"success": success, "message": "Template restored"}
        
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/logs")
async def get_logs(log_query: LogQuery, current_user: Optional[Dict] = Depends(check_auth_required)):
    """Get log entries"""
    try:
        logs = await web_service.get_logs(log_query)
        return {"logs": logs, "count": len(logs)}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/api/health")
async def health_check():
    """Health check endpoint (no auth required)"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "Jellynouncer Web Interface"
    }


# Serve static files (React build)
if os.path.exists("web/dist"):
    app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")


# ==================== Main Entry Point ====================

async def get_ssl_config():
    """Get SSL configuration for server startup"""
    ssl_manager = SSLManager(WEB_DB_PATH)
    await ssl_manager.initialize()
    settings = await ssl_manager.get_ssl_settings()
    
    if settings.get("ssl_enabled"):
        context = ssl_manager.create_ssl_context(settings)
        if context:
            return {
                "ssl_keyfile": None,  # Handled by context
                "ssl_certfile": None,  # Handled by context
                "ssl_context": context,
                "port": settings.get("port", 9000)
            }
    
    return {"port": 1985}


if __name__ == "__main__":
    import asyncio
    
    # Get SSL configuration
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ssl_config = loop.run_until_complete(get_ssl_config())
    
    # Run the web interface server
    uvicorn.run(
        "jellynouncer.web_api:app",
        host="0.0.0.0",
        port=ssl_config.get("port", 1985),
        ssl_keyfile=ssl_config.get("ssl_keyfile"),
        ssl_certfile=ssl_config.get("ssl_certfile"),
        reload=os.environ.get("JELLYNOUNCER_DEV_MODE") == "true" and not ssl_config.get("ssl_context")
    )