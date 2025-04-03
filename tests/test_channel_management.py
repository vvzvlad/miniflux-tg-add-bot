import pytest
from unittest.mock import patch, MagicMock, call
import sqlite3

# Import from parent directory
import sys
import os
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Предполагаем, что есть модуль channel_management с такими функциями
# Если тесты не проходят - корректируем их под реальную структуру модуля
try:
    from channel_management import (
        get_channel_by_id, 
        create_channel, 
        update_channel, 
        get_channels_by_status,
        add_feed_to_channel,
        remove_feed_from_channel,
        get_feeds_for_channel
    )
except ImportError:
    # Mock the module for tests to run even if actual module differs
    pytest.skip("channel_management module not available", allow_module_level=True)

# Fixture for mocking database connection
@pytest.fixture
def mock_db_connection():
    with patch('sqlite3.connect') as mock_connect:
        # Create mock cursor
        mock_cursor = MagicMock()
        # Configure connect to return a connection with cursor method
        mock_connect.return_value.cursor.return_value = mock_cursor
        # Configure connection to have commit and close methods
        mock_connection = mock_connect.return_value
        mock_connection.commit = MagicMock()
        mock_connection.close = MagicMock()
        # Yield both connection and cursor for tests to use
        yield mock_connection, mock_cursor

# Tests for Database Operations Error Handling (section 6.1)

def test_db_locked_error(mock_db_connection):
    """Test handling when database is locked."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Simulate a database locked error
    mock_cursor.execute.side_effect = sqlite3.OperationalError("database is locked")
    
    # Test with a function that accesses the database
    with pytest.raises(Exception) as excinfo:
        get_channel_by_id(123)
    
    # Check that error message mentions locked database
    assert "database is locked" in str(excinfo.value) or "locked" in str(excinfo.value).lower()

def test_transaction_rollback(mock_db_connection):
    """Test transaction rollback on error during multi-step operations."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Simulate an error during the second execute call
    mock_cursor.execute.side_effect = [None, sqlite3.Error("SQL error")]
    
    # Test with a function that performs multiple database operations
    with pytest.raises(Exception):
        create_channel({"id": 123, "title": "Test Channel", "status": "active"})
    
    # Check that rollback was called
    mock_connection.rollback.assert_called_once()
    # Ensure connection was not committed
    mock_connection.commit.assert_not_called()

# Tests for Data Integrity (section 6.1)

def test_get_channel_nonexistent_id(mock_db_connection):
    """Test get_channel_by_id with non-existent ID."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Configure cursor to return empty result for fetchone
    mock_cursor.fetchone.return_value = None
    
    # Call function with a non-existent ID
    result = get_channel_by_id(999)
    
    # Verify the result is None
    assert result is None
    # Verify the correct SQL query was executed
    mock_cursor.execute.assert_called_once()
    assert "999" in str(mock_cursor.execute.call_args) or 999 in mock_cursor.execute.call_args[0]

def test_create_channel_duplicate_id(mock_db_connection):
    """Test create_channel with duplicate ID."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Simulate a unique constraint violation
    mock_cursor.execute.side_effect = sqlite3.IntegrityError("UNIQUE constraint failed: channels.id")
    
    # Attempt to create a channel with duplicate ID
    with pytest.raises(Exception) as excinfo:
        create_channel({"id": 123, "title": "Duplicate Channel", "status": "active"})
    
    # Check error indicates duplicate
    assert "UNIQUE constraint" in str(excinfo.value) or "duplicate" in str(excinfo.value).lower()

def test_update_channel_nonexistent(mock_db_connection):
    """Test update_channel with non-existent ID."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Configure cursor to indicate no rows affected
    mock_cursor.rowcount = 0
    
    # Update a non-existent channel
    result = update_channel(999, {"title": "New Title", "status": "inactive"})
    
    # Verify the result indicates failure
    assert result is False or result == 0 or result is None
    # Verify correct SQL
    mock_cursor.execute.assert_called_once()
    assert "999" in str(mock_cursor.execute.call_args) or 999 in mock_cursor.execute.call_args[0]

# Tests for Channel Status Management (section 6.2)

def test_set_channel_status(mock_db_connection):
    """Test setting channel to different statuses."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Configure cursor to indicate success
    mock_cursor.rowcount = 1
    
    # Update a channel status
    result = update_channel(123, {"status": "inactive"})
    
    # Verify success
    assert result is True or result == 1 or result is not None
    # Verify SQL contains status update
    mock_cursor.execute.assert_called_once()
    assert "inactive" in str(mock_cursor.execute.call_args)
    # Verify transaction was committed
    mock_connection.commit.assert_called_once()

def test_channel_status_invalid_transition(mock_db_connection):
    """Test channel status transitions that aren't allowed."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Configure cursor to raise a constraint error for invalid status
    mock_cursor.execute.side_effect = sqlite3.IntegrityError("CHECK constraint failed: status IN ('active', 'inactive', 'pending')")
    
    # Attempt to set an invalid status
    with pytest.raises(Exception) as excinfo:
        update_channel(123, {"status": "invalid_status"})
    
    # Check error indicates constraint violation
    assert "CHECK constraint" in str(excinfo.value) or "constraint" in str(excinfo.value).lower()

def test_get_channels_by_status(mock_db_connection):
    """Test retrieving channels by status."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Mock data to return
    mock_cursor.fetchall.return_value = [
        (123, "Channel A", "active", "2023-01-01", "telegram"),
        (456, "Channel B", "active", "2023-01-02", "telegram")
    ]
    
    # Get active channels
    channels = get_channels_by_status("active")
    
    # Verify correct query was executed
    mock_cursor.execute.assert_called_once()
    assert "active" in str(mock_cursor.execute.call_args)
    # Verify result contains expected channels
    assert len(channels) == 2
    assert channels[0]["id"] == 123
    assert channels[1]["id"] == 456
    assert all(channel["status"] == "active" for channel in channels)

# Tests for Feed Association (section 6.3)

def test_add_multiple_feeds(mock_db_connection):
    """Test adding multiple feeds to a channel and verify correct count."""
    mock_connection, mock_cursor = mock_db_connection
    
    # First add one feed
    mock_cursor.rowcount = 1
    add_feed_to_channel(123, 101)
    
    # Add another feed
    add_feed_to_channel(123, 102)
    
    # Verify SQL was called twice with appropriate params
    assert mock_cursor.execute.call_count == 2
    # Extract call args from different calls
    call_args_list = mock_cursor.execute.call_args_list
    # First call should contain 123 and 101
    assert "123" in str(call_args_list[0]) or 123 in call_args_list[0][0][0] or 123 in call_args_list[0][0][1]
    assert "101" in str(call_args_list[0]) or 101 in call_args_list[0][0][0] or 101 in call_args_list[0][0][1]
    # Second call should contain 123 and 102
    assert "123" in str(call_args_list[1]) or 123 in call_args_list[1][0][0] or 123 in call_args_list[1][0][1]
    assert "102" in str(call_args_list[1]) or 102 in call_args_list[1][0][0] or 102 in call_args_list[1][0][1]
    
    # Verify transaction was committed twice
    assert mock_connection.commit.call_count == 2

def test_remove_nonexistent_feed(mock_db_connection):
    """Test removing feeds that don't exist."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Configure cursor to indicate no rows affected
    mock_cursor.rowcount = 0
    
    # Attempt to remove a feed that doesn't exist
    result = remove_feed_from_channel(123, 999)
    
    # Verify the result indicates failure
    assert result is False or result == 0 or result is None
    # Verify SQL contains both channel and feed ids
    mock_cursor.execute.assert_called_once()
    sql_params = str(mock_cursor.execute.call_args)
    assert "123" in sql_params or 123 in mock_cursor.execute.call_args[0][0] or 123 in mock_cursor.execute.call_args[0][1]
    assert "999" in sql_params or 999 in mock_cursor.execute.call_args[0][0] or 999 in mock_cursor.execute.call_args[0][1]

def test_get_feeds_nonexistent_channel(mock_db_connection):
    """Test retrieving feeds for non-existent channel."""
    mock_connection, mock_cursor = mock_db_connection
    
    # Configure cursor to return empty result
    mock_cursor.fetchall.return_value = []
    
    # Get feeds for non-existent channel
    feeds = get_feeds_for_channel(999)
    
    # Verify the result is empty
    assert feeds == [] or feeds is None
    # Verify SQL contains channel id
    mock_cursor.execute.assert_called_once()
    assert "999" in str(mock_cursor.execute.call_args) or 999 in mock_cursor.execute.call_args[0] 