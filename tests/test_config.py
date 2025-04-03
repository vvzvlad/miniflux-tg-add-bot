import os
import pytest
from unittest.mock import patch
import logging

# Import from parent directory
import sys
import os
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import to test
from config import load_config, initialize_miniflux_client, is_admin, should_accept_channels_without_username

# --- Tests for load_config function ---

def test_load_config_minimal_valid():
    """Test load_config with minimal valid configuration (required fields only)."""
    mock_env = {
        "MINIFLUX_BASE_URL": "http://miniflux.example.com",
        "MINIFLUX_API_KEY": "test_api_key",
        "TELEGRAM_TOKEN": "test_telegram_token",
    }
    
    with patch.dict('os.environ', mock_env, clear=True):
        config = load_config()
        
        # Check required fields
        assert config["MINIFLUX_BASE_URL"] == "http://miniflux.example.com"
        assert config["MINIFLUX_API_KEY"] == "test_api_key"
        assert config["TELEGRAM_TOKEN"] == "test_telegram_token"
        
        # Check optional fields have default values
        assert config["ADMIN_USERNAME"] is None
        assert config["RSS_BRIDGE_URL"] is None
        assert config["ACCEPT_CHANNELS_WITHOUT_USERNAME_STR"] == "false"

def test_load_config_username_password():
    """Test load_config with username/password instead of API key."""
    mock_env = {
        "MINIFLUX_BASE_URL": "http://miniflux.example.com",
        "MINIFLUX_USERNAME": "test_user",
        "MINIFLUX_PASSWORD": "test_password",
        "TELEGRAM_TOKEN": "test_telegram_token",
    }
    
    with patch.dict('os.environ', mock_env, clear=True):
        config = load_config()
        
        # Check credentials
        assert config["MINIFLUX_USERNAME"] == "test_user"
        assert config["MINIFLUX_PASSWORD"] == "test_password"
        assert config["MINIFLUX_API_KEY"] is None

def test_load_config_all_fields():
    """Test load_config with all fields populated."""
    mock_env = {
        "MINIFLUX_BASE_URL": "http://miniflux.example.com",
        "MINIFLUX_API_KEY": "test_api_key",
        "TELEGRAM_TOKEN": "test_telegram_token",
        "ADMIN": "test_admin",
        "RSS_BRIDGE_URL": "http://rssbridge.example.com/",
        "RSS_BRIDGE_TOKEN": "test_bridge_token",
        "ACCEPT_CHANNELS_WITHOUT_USERNAME": "true"
    }
    
    with patch.dict('os.environ', mock_env, clear=True):
        config = load_config()
        
        # Check all fields
        assert config["MINIFLUX_BASE_URL"] == "http://miniflux.example.com"
        assert config["MINIFLUX_API_KEY"] == "test_api_key"
        assert config["TELEGRAM_TOKEN"] == "test_telegram_token"
        assert config["ADMIN_USERNAME"] == "test_admin"
        assert config["RSS_BRIDGE_URL"] == "http://rssbridge.example.com/"
        assert config["RSS_BRIDGE_TOKEN"] == "test_bridge_token"
        assert config["ACCEPT_CHANNELS_WITHOUT_USERNAME_STR"] == "true"

def test_load_config_missing_miniflux_url():
    """Test load_config validation when MINIFLUX_BASE_URL is missing."""
    mock_env = {
        "MINIFLUX_API_KEY": "test_api_key",
        "TELEGRAM_TOKEN": "test_telegram_token",
    }
    
    with patch.dict('os.environ', mock_env, clear=True):
        with pytest.raises(ValueError, match="Missing MINIFLUX_BASE_URL"):
            load_config()

def test_load_config_missing_credentials():
    """Test load_config validation when all Miniflux credentials are missing."""
    mock_env = {
        "MINIFLUX_BASE_URL": "http://miniflux.example.com",
        "TELEGRAM_TOKEN": "test_telegram_token",
    }
    
    with patch.dict('os.environ', mock_env, clear=True):
        with pytest.raises(ValueError, match="Missing Miniflux credentials"):
            load_config()

def test_load_config_missing_telegram_token():
    """Test load_config validation when TELEGRAM_TOKEN is missing."""
    mock_env = {
        "MINIFLUX_BASE_URL": "http://miniflux.example.com",
        "MINIFLUX_API_KEY": "test_api_key",
    }
    
    with patch.dict('os.environ', mock_env, clear=True):
        with pytest.raises(ValueError, match="Missing TELEGRAM_TOKEN"):
            load_config()

# --- Tests for initialize_miniflux_client function ---

@patch('miniflux.Client')
def test_initialize_miniflux_client_with_api_key(mock_client):
    """Test initialize_miniflux_client when API key is provided."""
    config = {
        "MINIFLUX_BASE_URL": "http://miniflux.example.com",
        "MINIFLUX_API_KEY": "test_api_key",
        "MINIFLUX_USERNAME": None,
        "MINIFLUX_PASSWORD": None,
    }
    
    initialize_miniflux_client(config)
    
    # Check that Client was called with correct parameters
    mock_client.assert_called_once_with("http://miniflux.example.com", api_key="test_api_key")

@patch('miniflux.Client')
def test_initialize_miniflux_client_with_username_password(mock_client):
    """Test initialize_miniflux_client when username/password is provided."""
    config = {
        "MINIFLUX_BASE_URL": "http://miniflux.example.com",
        "MINIFLUX_API_KEY": None,
        "MINIFLUX_USERNAME": "test_user",
        "MINIFLUX_PASSWORD": "test_password",
    }
    
    initialize_miniflux_client(config)
    
    # Check that Client was called with correct parameters
    mock_client.assert_called_once_with("http://miniflux.example.com", username="test_user", password="test_password")

@patch('miniflux.Client')
def test_initialize_miniflux_client_exception(mock_client):
    """Test initialize_miniflux_client when an exception occurs."""
    config = {
        "MINIFLUX_BASE_URL": "http://miniflux.example.com",
        "MINIFLUX_API_KEY": "test_api_key",
        "MINIFLUX_USERNAME": None,
        "MINIFLUX_PASSWORD": None,
    }
    
    # Make Client raise an exception
    mock_client.side_effect = Exception("Connection failed")
    
    with pytest.raises(ConnectionError):
        initialize_miniflux_client(config)

# --- Tests for helper functions ---

@patch('config.ADMIN_USERNAME', 'admin_user')
def test_is_admin_valid():
    """Test is_admin function with a valid admin username."""
    assert is_admin('admin_user') is True

@patch('config.ADMIN_USERNAME', 'admin_user')
def test_is_admin_invalid():
    """Test is_admin function with an invalid username."""
    assert is_admin('not_admin') is False

@patch('config.ADMIN_USERNAME', 'admin_user')
def test_is_admin_none():
    """Test is_admin function with None username."""
    assert is_admin(None) is False

@patch('config.ADMIN_USERNAME', None)
def test_is_admin_admin_not_set():
    """Test is_admin function when ADMIN_USERNAME is not set."""
    assert is_admin('any_user') is False

@patch('config.ACCEPT_CHANNELS_WITHOUT_USERNAME_STR', 'true')
def test_should_accept_channels_without_username_true():
    """Test should_accept_channels_without_username when set to true."""
    assert should_accept_channels_without_username() is True

@patch('config.ACCEPT_CHANNELS_WITHOUT_USERNAME_STR', 'false')
def test_should_accept_channels_without_username_false():
    """Test should_accept_channels_without_username when set to false."""
    assert should_accept_channels_without_username() is False

@patch('config.ACCEPT_CHANNELS_WITHOUT_USERNAME_STR', 'TRUE')
def test_should_accept_channels_without_username_case_insensitive():
    """Test should_accept_channels_without_username is case insensitive."""
    assert should_accept_channels_without_username() is True 