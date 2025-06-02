import logging
from typing import Dict, Any, Optional

from .rtcconfiguration import RTCSocks5Proxy

logger = logging.getLogger(__name__)

__all__ = ["create_socks5_proxy_config", "enable_socks5_support", "validate_socks5_proxy", "log_socks5_configuration"]


def create_socks5_proxy_config(proxy: RTCSocks5Proxy) -> Dict[str, Any]:
    """
    Create a SOCKS5 proxy configuration dictionary for aioice.
    
    This function converts an RTCSocks5Proxy object to the dictionary format
    expected by aioice's Connection class.
    
    :param proxy: RTCSocks5Proxy configuration object
    :return: Dictionary configuration for aioice
    """
    if proxy is None:
        return None
        
    config = {
        'host': proxy.host,
        'port': proxy.port,
    }
    
    if proxy.username is not None:
        config['username'] = proxy.username
    
    if proxy.password is not None:
        config['password'] = proxy.password
        
    return config


def enable_socks5_support() -> None:
    """
    Enable SOCKS5 support in the aioice library.
    
    This is a no-op function in the current implementation as aioice
    natively supports SOCKS5 proxying. It's included for API completeness
    and potential future use.
    """
    logger.debug("SOCKS5 support is natively enabled in aioice")


def log_socks5_configuration(proxy: Optional[RTCSocks5Proxy]) -> None:
    """
    Log SOCKS5 proxy configuration details.
    
    This helper function logs the SOCKS5 proxy configuration at debug level,
    masking sensitive information like passwords.
    
    :param proxy: RTCSocks5Proxy configuration object or None
    """
    if proxy is None:
        logger.debug("No SOCKS5 proxy configured")
        return
        
    auth_type = "none"
    if proxy.username is not None and proxy.password is not None:
        auth_type = "username/password"
        
    logger.debug(
        "SOCKS5 proxy configured: %s:%d (auth: %s)",
        proxy.host,
        proxy.port,
        auth_type
    )


class Socks5Error(Exception):
    """Exception raised for SOCKS5-related errors."""
    pass


def validate_socks5_proxy(proxy: RTCSocks5Proxy) -> None:
    """
    Validate a SOCKS5 proxy configuration.
    
    Checks that the proxy configuration is valid and raises appropriate
    exceptions if not.
    
    :param proxy: RTCSocks5Proxy configuration object
    :raises Socks5Error: If the proxy configuration is invalid
    """
    if not proxy.host:
        raise Socks5Error("SOCKS5 proxy host cannot be empty")
        
    if not isinstance(proxy.port, int) or proxy.port <= 0 or proxy.port > 65535:
        raise Socks5Error(f"Invalid SOCKS5 proxy port: {proxy.port}")
        
    # If username is provided, password must also be provided
    if proxy.username is not None and proxy.password is None:
        raise Socks5Error("SOCKS5 proxy password must be provided when username is set")
