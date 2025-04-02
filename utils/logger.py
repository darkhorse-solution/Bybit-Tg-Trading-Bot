#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Logger module for setting up and managing logging.
"""

import os
import logging
import datetime
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Create formatters
file_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
console_formatter = logging.Formatter(
    '%(asctime)s - %(name)-12s - %(levelname)-8s - %(message)s',
    datefmt='%H:%M:%S'
)

# File handler (with rotation)
file_handler = RotatingFileHandler(
    "logs/trading_bot.log",
    maxBytes=10*1024*1024,  # 10 MB
    backupCount=5
)
file_handler.setFormatter(file_formatter)
file_handler.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)
console_handler.setLevel(logging.INFO)

# Add handlers to root logger
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# Create main logger
logger = logging.getLogger('trading_bot')
logger.setLevel(logging.INFO)

# Export the logger
logger.info("Logging initialized")

def setup_logger(config=None):
    """
    Set up and configure the logger for the application.
    
    Args:
        config: Configuration object (optional)
        
    Returns:
        The configured logger
    """
    global logger, root_logger, file_handler, console_handler
    
    # Get log level from config or use default
    log_level_str = "INFO"
    log_file = "logs/trading_bot.log"
    
    if config:
        log_level_str = config.get('LOG_LEVEL', log_level_str)
        log_file = config.get('LOG_FILE', log_file)
        
        # Ensure log file is within logs directory
        if not log_file.startswith('logs/'):
            log_file = os.path.join('logs', log_file)
    
    # Map string level to logging level
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    
    # Update existing handlers
    root_logger.setLevel(log_level)
    file_handler.setLevel(log_level)
    console_handler.setLevel(log_level)
    logger.setLevel(log_level)
    
    # If log file changed, create a new file handler
    if log_file != file_handler.baseFilename:
        # Remove existing file handler
        root_logger.removeHandler(file_handler)
        
        # Create new file handler
        new_file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,  # 10 MB
            backupCount=5
        )
        new_file_handler.setFormatter(file_formatter)
        new_file_handler.setLevel(log_level)
        root_logger.addHandler(new_file_handler)
        
        # Update global file_handler
        file_handler = new_file_handler
    
    # Create specific loggers for components
    component_loggers = {
        'bot': logging.getLogger('bot'),
        'trader': logging.getLogger('trader'),
        'signal': logging.getLogger('signal'),
        'risk': logging.getLogger('risk'),
        'symbol_mapper': logging.getLogger('symbol_mapper'),
        'config': logging.getLogger('config')
    }
    
    # Set all component loggers to the same level
    for component_logger in component_loggers.values():
        component_logger.setLevel(log_level)
    
    logger.info(f"Logging reconfigured at level {log_level_str}")
    
    return logger


def get_telegram_notification_handler(config):
    """
    Create a custom handler for sending critical logs to Telegram.
    
    Args:
        config: Configuration object
        
    Returns:
        Telegram notification handler or None if not configured
    """
    if not config:
        return None
        
    # Check if Telegram notifications are enabled
    enabled = config.get('ENABLE_TELEGRAM_NOTIFICATIONS', 'False').lower() == 'true'
    if not enabled:
        return None
        
    # Get Telegram bot token and chat ID
    token = config.get('NOTIFICATION_TELEGRAM_TOKEN', '')
    chat_id = config.get('NOTIFICATION_TELEGRAM_CHAT_ID', '')
    
    if not token or not chat_id:
        logger.warning(
            "Telegram notifications enabled but token or chat ID not set"
        )
        return None
    
    # Create a custom handler for Telegram notifications
    class TelegramHandler(logging.Handler):
        def emit(self, record):
            try:
                # Only send critical, error, and warning logs
                if record.levelno < logging.WARNING:
                    return
                    
                # Format the log message
                message = self.format(record)
                
                # Send message to Telegram (using requests to avoid dependencies)
                import requests
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = {
                    "chat_id": chat_id,
                    "text": f"⚠️ *ALERT*: {message}",
                    "parse_mode": "Markdown"
                }
                
                requests.post(url, data=data, timeout=5)
            except Exception as e:
                # Don't use logging here to avoid infinite loop
                print(f"Error sending Telegram notification: {str(e)}")
    
    # Create and configure the handler
    telegram_handler = TelegramHandler()
    telegram_handler.setLevel(logging.WARNING)
    telegram_handler.setFormatter(logging.Formatter(
        '%(levelname)s - %(name)s - %(message)s'
    ))
    
    return telegram_handler

def log_trade_execution(signal, order_result):
    """
    Log details of a trade execution.
    
    Args:
        signal: The trading signal
        order_result: Result from order placement
    """
    logger.info(
        f"Trade executed: {signal.get('position_type', '').upper()} {signal.get('symbol', '')} "
        f"@ {signal.get('entry_price', 'Unknown')} | "
        f"Order ID: {order_result.get('orderId', 'Unknown')}"
    )
    
    # Log additional details at debug level
    logger.debug(f"Full order result: {order_result}")
    logger.debug(f"Signal details: {signal}")

def create_trade_log_file():
    """
    Create a CSV log file for tracking trades.
    
    Returns:
        Path to the log file
    """
    # Create trades directory if it doesn't exist
    os.makedirs('logs/trades', exist_ok=True)
    
    # Create a file with current date in the name
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    file_path = f"logs/trades/trades_{today}.csv"
    
    # If file doesn't exist, create it with headers
    if not os.path.exists(file_path):
        with open(file_path, 'w') as f:
            headers = [
                "Timestamp", "Symbol", "Direction", "Entry", "Stop Loss",
                "Take Profit", "Position Size", "Order ID", "Status"
            ]
            f.write(','.join(headers) + '\n')
    
    return file_path

def log_trade_to_csv(trade_data):
    """
    Log a trade to the CSV file.
    
    Args:
        trade_data: Dictionary containing trade information
        
    Returns:
        True if successful, False otherwise
    """
    try:
        file_path = create_trade_log_file()
        
        # Format the row
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        row = [
            now,
            trade_data.get('symbol', ''),
            trade_data.get('direction', ''),
            str(trade_data.get('entry', '')),
            str(trade_data.get('stop_loss', '')),
            str(trade_data.get('take_profit', '')),
            str(trade_data.get('position_size', '')),
            str(trade_data.get('order_id', '')),
            trade_data.get('status', 'EXECUTED')
        ]
        
        # Write to file
        with open(file_path, 'a') as f:
            f.write(','.join(row) + '\n')
            
        return True
    except Exception as e:
        logger.error(f"Error logging trade to CSV: {str(e)}")
        return False