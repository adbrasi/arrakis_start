#!/usr/bin/env python3
"""
WebSocket Server - Real-time updates for Arrakis Start
Streams download progress, logs, and status updates to UI
Thread-safe implementation using a message queue
"""

import asyncio
import json
import logging
import queue
import threading
from typing import Set, Dict, Any
import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger(__name__)

# Connected clients
_clients: Set[WebSocketServerProtocol] = set()

# Thread-safe message queue for cross-thread communication
_message_queue: queue.Queue = queue.Queue()
_loop: asyncio.AbstractEventLoop = None


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


async def process_message_queue():
    """Process messages from the thread-safe queue"""
    while True:
        try:
            # Non-blocking check
            while True:
                try:
                    message = _message_queue.get_nowait()
                    try:
                        await broadcast(message)
                    except Exception as broadcast_err:
                        logger.error(f"WebSocket broadcast failed: {broadcast_err}")
                except queue.Empty:
                    break
            await asyncio.sleep(0.1)  # Small delay to prevent busy-waiting
        except Exception as e:
            logger.error(f"Error processing message queue: {e}")
            await asyncio.sleep(0.5)


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
        _clients.discard(websocket)


async def start_websocket_server(host: str = "0.0.0.0", port: int = 8091):
    """Start WebSocket server with message queue processor"""
    global _loop
    _loop = asyncio.get_running_loop()
    
    # Start message queue processor
    asyncio.create_task(process_message_queue())
    
    async with websockets.serve(handle_client, host, port):
        logger.info(f"WebSocket server running on ws://{host}:{port}")
        await asyncio.Future()  # Run forever


# Thread-safe event emitters (can be called from any thread)
def _queue_message(message: Dict[str, Any]):
    """Queue a message for broadcast (thread-safe)"""
    _message_queue.put(message)


def send_download_progress(filename: str, percent: float, speed: str = "", eta: str = ""):
    """Send download progress update (thread-safe)"""
    _queue_message({
        "type": "download_progress",
        "filename": filename,
        "percent": percent,
        "speed": speed,
        "eta": eta
    })


def send_install_status(status: str, message: str):
    """Send installation status update (thread-safe)"""
    _queue_message({
        "type": "install_status",
        "status": status,
        "message": message
    })


def send_comfyui_status(status: str, port: int = 8818, pid: int = None):
    """Send ComfyUI status update (thread-safe)"""
    _queue_message({
        "type": "comfyui_status",
        "status": status,
        "port": port,
        "pid": pid
    })


def send_log_message(level: str, message: str):
    """Send log message (thread-safe)"""
    _queue_message({
        "type": "log",
        "level": level,
        "message": message
    })


def send_install_complete(success: bool, presets: list):
    """Send installation complete notification (thread-safe)"""
    _queue_message({
        "type": "install_complete",
        "success": success,
        "presets": presets
    })
