#!/usr/bin/env python3
"""
WebSocket Server - Real-time updates for Arrakis Start
Streams download progress, logs, and status updates to UI
"""

import asyncio
import json
import logging
from typing import Set, Dict, Any
import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger(__name__)

# Connected clients
_clients: Set[WebSocketServerProtocol] = set()


async def broadcast(message: Dict[str, Any]):
    """Broadcast message to all connected clients"""
    if not _clients:
        return
    
    message_json = json.dumps(message)
    # Send to all clients concurrently
    await asyncio.gather(
        *[client.send(message_json) for client in _clients],
        return_exceptions=True
    )


async def handle_client(websocket: WebSocketServerProtocol):
    """Handle WebSocket client connection"""
    _clients.add(websocket)
    logger.info(f"WebSocket client connected ({len(_clients)} total)")
    
    try:
        # Send initial connection confirmation
        await websocket.send(json.dumps({
            "type": "connected",
            "message": "WebSocket connected"
        }))
        
        # Keep connection alive and handle incoming messages
        async for message in websocket:
            try:
                data = json.loads(message)
                # Handle client messages if needed
                logger.debug(f"Received from client: {data}")
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from client: {message}")
    
    except websockets.exceptions.ConnectionClosed:
        logger.info("WebSocket client disconnected")
    finally:
        _clients.remove(websocket)


async def start_websocket_server(host: str = "0.0.0.0", port: int = 8091):
    """Start WebSocket server"""
    async with websockets.serve(handle_client, host, port):
        logger.info(f"WebSocket server running on ws://{host}:{port}")
        await asyncio.Future()  # Run forever


# Event emitters for different types of updates
def send_download_progress(filename: str, percent: float, speed: str = "", eta: str = ""):
    """Send download progress update"""
    asyncio.create_task(broadcast({
        "type": "download_progress",
        "filename": filename,
        "percent": percent,
        "speed": speed,
        "eta": eta
    }))


def send_install_status(status: str, message: str):
    """Send installation status update"""
    asyncio.create_task(broadcast({
        "type": "install_status",
        "status": status,
        "message": message
    }))


def send_comfyui_status(status: str, port: int = 8818, pid: int = None):
    """Send ComfyUI status update"""
    asyncio.create_task(broadcast({
        "type": "comfyui_status",
        "status": status,
        "port": port,
        "pid": pid
    }))


def send_log_message(level: str, message: str):
    """Send log message"""
    asyncio.create_task(broadcast({
        "type": "log",
        "level": level,
        "message": message
    }))


def send_install_complete(success: bool, presets: list):
    """Send installation complete notification"""
    asyncio.create_task(broadcast({
        "type": "install_complete",
        "success": success,
        "presets": presets
    }))
