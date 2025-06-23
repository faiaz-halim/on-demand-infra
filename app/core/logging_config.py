import logging
# import sys
# from pythonjsonlogger.json import JsonFormatter
# from app.core.config import settings # Import your settings

# # Get the log level from settings, default to INFO if not set or invalid
# log_level_str = settings.LOG_LEVEL.upper()
# log_level = getattr(logging, log_level_str, logging.INFO)

# # Create a logger
# logger = logging.getLogger("app_server_logger")
# logger.setLevel(log_level)

# # Create a handler for stdout
# handler = logging.StreamHandler(sys.stdout)

# # Create a JSON formatter
# # Example format: {"timestamp": "...", "level": "...", "name": "...", "message": "...", "file": "...", "line": ..., "func": "..."}
# formatter = JsonFormatter(
#     fmt="%(asctime)s %(levelname)s %(name)s %(module)s %(funcName)s %(lineno)d %(message)s"
# )

# handler.setFormatter(formatter)

# # Add the handler to the logger
# # Prevent duplicate handlers if this module is reloaded, e.g., by Uvicorn's reloader
# if not logger.handlers:
#     logger.addHandler(handler)

# Function to get the configured logger
def get_logger(name: str):
    # Child loggers will inherit handlers and level from the parent if not configured otherwise
    return logging.getLogger(f"app_server_logger.{name}")

# Example of how to use it:
# from app.core.logging_config import get_logger
# logger = get_logger(__name__)
# logger.info("This is an info message.")
