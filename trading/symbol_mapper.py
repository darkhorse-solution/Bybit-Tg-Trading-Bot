# trading/bot.py
import os
import json
import asyncio
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from trading.signal import SignalParser, SignalFormatter
from trading.trader import BybitTrader
from trading.symbol_mapper import SymbolMapper
from utils.logger import logger
from utils.config import Config


class TradingBot:
    """
    Main trading bot that integrates Telegram client and Bybit trading.
    """

    def __init__(self):
        """
        Initialize the trading bot with all required components.
        """
        self.api_id = Config.API_ID
        self.api_hash = Config.API_HASH
        self.session_string = Config.SESSION_STRING
        self.source_channel_id = int(Config.SOURCE_CHANNEL_ID)
        self.target_channel_id = int(Config.TARGET_CHANNEL_ID)

        # Initialize the symbol mapper
        self.symbol_mapper = SymbolMapper()

        # Initialize components
        self.client = None
        self.trader = BybitTrader(
            Config.BYBIT_API_KEY, 
            Config.BYBIT_API_SECRET_KEY, 
            target_channel_id=self.target_channel_id if Config.ENABLE_FAILURE_NOTIFICATIONS else None,
            testnet=Config.BYBIT_TESTNET
        )
        self.parser = SignalParser()
        self.formatter = SignalFormatter()
        
        # Log loaded mappings
        logger.info(f"Loaded {len(self.symbol_mapper.mappings)} symbol mappings")

    async def start(self):
        """Start the Telegram client, set up handlers, and load active positions."""
        logger.info('Initializing Telegram client...')

        # Create and connect to Telegram
        self.client = TelegramClient(
            StringSession(self.session_string),
            self.api_id,
            self.api_hash,
            connection_retries=5,
            use_ipv6=True,
            timeout=30
        )

        await self.client.connect()
        await self._authenticate()
        
        # IMPORTANT: Set the telegram client in the trader here
        self.trader.set_telegram_client(self.client)
        self.trader.target_channel_id = self.target_channel_id  # Explicitly set target channel
        
        self._setup_handlers()
        
        # Load and monitor active positions from Bybit
        logger.info('Loading active positions from Bybit...')
        await self.trader.load_and_monitor_active_positions()
        
        logger.info('Bot started successfully')

    async def run(self):
        """Run the bot until disconnected."""
        await self.client.run_until_disconnected()

    async def stop(self):
        """Stop the bot and disconnect from Telegram."""
        if self.client and self.client.is_connected():
            logger.info('Disconnecting from Telegram...')
            await self.client.disconnect()
            logger.info('Disconnected')

    async def _authenticate(self):
        """Authenticate with Telegram if needed."""
        if not await self.client.is_user_authorized():
            # Phone number authentication
            phone = input('Phone number (include country code, e.g., +1234567890): ')
            logger.info(f'Sending code to: {phone}')
            await self.client.send_code_request(phone)

            # Code verification
            code = input('Enter the code you received: ')
            await self.client.sign_in(phone, code)

            if await self.client.is_user_authorized():
                logger.info('Authentication successful!')
                session_string = self.client.session.save()
                logger.info(f'Your session string (save this): {session_string}')
                
    def _setup_handlers(self):
        """Set up message handlers for the Telegram client."""
        from utils.config import Config

        @self.client.on(events.NewMessage(chats=[self.source_channel_id]))
        async def handle_new_message(event):
            message = event.message
            
            # Log the message details including whether it's a reply
            is_reply = message.reply_to is not None
            logger.info(f"New message received (reply: {is_reply}): {message.text[:50]}...")

            # Parse the signal regardless of whether it's a reply or not
            signal = self.parser.parse(message.text)

            if signal:
                # Format the signal for readability
                formatted_message = self.formatter.format(signal)
                
                # Process the message based on its type
                if signal.get('is_profit_message', False):
                    # For profit messages, we handle profit taking or SL adjustment
                    # regardless of whether it's a reply or not
                    logger.info(f"Processing profit message (reply: {is_reply}) for {signal.get('binance_symbol', 'unknown')}")
                    await self._handle_profit_message(signal, formatted_message)
                elif not is_reply:  # Only process new trade signals if they're not replies
                    # For trading signals, execute trades
                    logger.info(f"Processing trade signal for {signal.get('binance_symbol', 'unknown')}")
                    await self._execute_trades(signal)

                    # Send formatted message to target channel if not empty and entry notifications are disabled
                    # This prevents duplicate messages when entry notifications are enabled
                    if formatted_message and not Config.ENABLE_ENTRY_NOTIFICATIONS:
                        try:
                            await self.client.send_message(self.target_channel_id, formatted_message)
                            logger.info("Signal processed and forwarded successfully!")
                        except Exception as e:
                            logger.error(f"Error sending message: {e}")
                else:
                    logger.info(f"Skipping trade execution for reply message: {message.text[:50]}...")
            else:
                logger.info("Message received but not a valid signal")

    async def _handle_profit_message(self, signal, formatted_message):
        """
        Handle profit message signals - check symbol mapping, manage orders, and forward.
        
        Args:
            signal (dict): Parsed profit message signal
            formatted_message (str): Formatted message to forward
        """
        # Check if the symbol needs mapping
        symbol = signal['binance_symbol']
        mapped_symbol = None
        rate = 1.0
        
        try:
            # Use the symbol mapper
            mapped_symbol, rate = self.symbol_mapper.get_mapped_symbol(symbol)
            
            # If we found a mapping, update the symbol and prices
            if mapped_symbol:
                logger.info(f"Using mapped symbol for profit message: {symbol} -> {mapped_symbol} (rate: {rate})")
                
                # Update the original symbol
                original_symbol = symbol
                symbol = mapped_symbol
                
                # Adjust entry price if present
                if 'entry_price' in signal:
                    original_price = signal['entry_price']
                    signal['entry_price'] = original_price * rate
                    logger.info(f"Adjusted entry price: {original_price} -> {signal['entry_price']}")
                
                # Update the formatted message with mapped symbol
                formatted_message = formatted_message.replace(f"#{signal['binance_symbol']}", f"#{mapped_symbol}")
        except Exception as e:
            logger.error(f"Error checking symbol mapping for profit message: {e}")
        
        # Handle the profit message based on profit target percentage
        profit_target = signal.get('profit_target', 0)
        
        # If 100% profit target, fully exit the position
        if profit_target == 100:
            try:
                logger.info(f"Attempting to close position for {symbol} as 100% profit target reached")
                result = await self.trader.close_position(symbol)
                
                if result['success']:
                    logger.info(f"Successfully closed position for {symbol}: {result['message']}")
                    # Add to formatted message
                    formatted_message += f"\n\n✅ Position closed at 100% profit target"
                else:
                    logger.warning(f"Failed to close position for {symbol}: {result['message']}")
            except Exception as e:
                logger.error(f"Error closing position for {symbol}: {e}")
        
        # For other profit targets, adjust the stop loss
        elif profit_target > 0:
            try:
                logger.info(f"Attempting to adjust SL for {symbol} to lock in {profit_target}% profit")
                result = await self.trader.adjust_stop_loss_for_profit_target(symbol, profit_target)
                
                if result['success']:
                    logger.info(f"Successfully adjusted SL for {symbol}: {result['message']}")
                    
                    # Convert SL percentages back to original prices for display if needed
                    original_sl_percent = result.get('original_sl_percent')
                    new_sl_percent = result.get('new_sl_percent')
                    
                    # Add to formatted message
                    if original_sl_percent and new_sl_percent:
                        formatted_message += f"\n\n✅ Stop Loss adjusted from {original_sl_percent:.2f}% to {new_sl_percent:.2f}% to lock in profits"
                else:
                    logger.warning(f"Failed to adjust SL for {symbol}: {result['message']}")
            except Exception as e:
                logger.error(f"Error adjusting SL for {symbol}: {e}")
        
        # Forward the profit message to the target channel
        try:
            await self.client.send_message(self.target_channel_id, formatted_message)
            logger.info("Profit message forwarded successfully!")
        except Exception as e:
            logger.error(f"Error sending profit message: {e}")

    async def _execute_trades(self, signal):
        """
        Execute the trades based on the parsed signal.

        Args:
            signal (dict): Parsed trading signal
        """
        try:
            # Execute the trade with the signal data
            result = await self.trader.execute_signal(signal)
            logger.info(f"Trade execution result: {result}")
            return result
        except Exception as e:
            logger.error(f"Error executing trades: {e}")
            return None