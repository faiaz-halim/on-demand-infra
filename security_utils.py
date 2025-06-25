import re
import logging
from typing import Optional

# Configure logger
logger = logging.getLogger(__name__)

def validate_and_sanitize(input_str: str,
                         pattern: str,
                         max_length: int = 64,
                         context: str = "general") -> Optional[str]:
    """
    Validate and sanitize input based on regex pattern and length.
    Returns sanitized string or None if validation fails.
    Logs validation failures with context information.
    """
    if not input_str:
        return ""

    # Validate length
    if len(input_str) > max_length:
        logger.warning(f"Input validation failed in {context}: "
                      f"'{input_str[:30]}...' exceeds max length {max_length}")
        return None

    # Validate pattern
    if not re.fullmatch(pattern, input_str):
        logger.warning(f"Input validation failed in {context}: "
                      f"'{input_str[:30]}...' contains invalid characters. "
                      f"Input rejected.")
        return None

    return input_str

def sanitize_terraform_input(input_str: str, max_length: int = 64) -> Optional[str]:
    """
    Sanitize input for Terraform HCL to prevent injection attacks.
    Allows alphanumeric, hyphen, underscore, and dot.
    Returns None if input exceeds max_length or contains invalid characters.
    """
    return validate_and_sanitize(
        input_str,
        r'^[a-zA-Z0-9_\.\-]+$',
        max_length,
        "Terraform"
    )

def sanitize_kubernetes_input(input_str: str, max_length: int = 63) -> Optional[str]:
    """
    Sanitize input for Kubernetes manifests to prevent injection attacks.
    Kubernetes names have stricter requirements (RFC 1123).
    Returns None if input exceeds max_length or contains invalid characters.
    """
    return validate_and_sanitize(
        input_str,
        r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$',
        max_length,
        "Kubernetes"
    )

def sanitize_shell_input(input_str: str, max_length: int = 256) -> Optional[str]:
    """
    Sanitize input for shell commands to prevent injection attacks.
    Only allows safe characters for shell arguments.
    Returns None if input exceeds max_length or contains invalid characters.
    """
    return validate_and_sanitize(
        input_str,
        r'^[a-zA-Z0-9_\.\-\/\:= ]+$',
        max_length,
        "Shell"
    )
