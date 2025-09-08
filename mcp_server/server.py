#!/usr/bin/env python3
"""Unity integration through the Model Context Protocol."""

import argparse
import asyncio
import json
import logging
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional
from mcp.server.fastmcp import Context, FastMCP

from typing import Union

__version__ = "1.0.0"

# Configure logging 输出日志用
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("unity_mcp")


@dataclass
class UnityConnection:
    host: str
    port: int
    sock: socket.socket = None

    def connect(self) -> bool:
        """Connect to the Unity plugin socket server"""
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Unity at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Unity: {str(e)}")
            self.sock = None
            return False

    def disconnect(self):
        """Disconnect from the Unity plugin"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Unity: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        sock.settimeout(15.0)

        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break

                    chunks.append(chunk)

                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise

        # If we get here, we either timed out or broke out of the loop
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Unity and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Unity")

        command = {
            "type": command_type,
            "parameters": json.dumps(params or {})
        }

        try:
            logger.info(f"Sending command: {command_type} with params: {params}")

            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")

            self.sock.settimeout(15.0)

            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            logger.info(response_data.decode('utf-8'))

            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('success', 'unknown')}")

            if not response.get("success"):
                logger.error(f"Unity error: {response.get('error')}")
                raise Exception(response.get("error", "Unknown error from Unity"))

            return response.get("data", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Unity")
            self.sock = None
            raise Exception("Timeout waiting for Unity response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Unity lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Unity: {str(e)}")
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Unity: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Unity: {str(e)}")
            self.sock = None
            raise Exception(f"Communication error with Unity: {str(e)}")


# Global connection instance
_unity_connection = None


def get_unity_connection():
    """Get or create a connection to Unity"""
    global _unity_connection

    if _unity_connection is None:
        _unity_connection = UnityConnection(host="localhost", port=9876)

    # Try to connect if not already connected
    if not _unity_connection.connect():
        # If connection fails, create a new connection and try again
        _unity_connection = UnityConnection(host="localhost", port=9876)
        if not _unity_connection.connect():
            raise ConnectionError("Failed to connect to Unity")

    return _unity_connection


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Lifecycle manager for the MCP server
    MCP 服务的 “开机—运行—关机”全过程管理器
    启动阶段：
    打印启动日志
    尝试连接 Unity

    运行阶段：
    MCP 服务保持运行
    返回状态字典 "running" 给 MCP 框架

    关闭阶段：
    打印关闭日志
    断开 Unity 连接，进行资源清理 
    """
    # Setup phase
    logger.info("Starting Unity MCP server")

    # Try to establish a connection to Unity
    try:
        connection = get_unity_connection()
        logger.info("Connected to Unity successfully")
    except Exception as e:
        logger.warning(f"Could not connect to Unity at startup: {str(e)}")
        logger.warning("Will try to connect when commands are received")

    try:
        yield {"status": "running"}
    finally:
        # Cleanup phase
        logger.info("Shutting down Unity MCP server")

        # Disconnect from Unity
        if _unity_connection:
            _unity_connection.disconnect()


# MCP Tools 定义服务端MCP工具
# Create the MCP server with lifespan support
mcp = FastMCP(
    title="Unity MCP",
    description="Unity integration through the Model Context Protocol",
    version=__version__,
    lifespan=server_lifespan,
)


@mcp.tool()
def get_system_info(ctx: Context) -> str:
    """Get information about the Unity system.

    Returns:
        A JSON string containing information about the Unity system, including version, platform, etc.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("core.GetSystemInfo")
        return json.dumps(result)
    except Exception as e:
        return f"Error getting system info: {str(e)}"


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get information about the current Unity scene.

    Returns:
        A JSON string containing information about the scene, including objects, cameras, and lights.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("scene.GetSceneInfo")
        return json.dumps(result)
    except Exception as e:
        return f"Error getting scene info: {str(e)}"


@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """Get detailed information about a specific object in the Unity scene.

    Args:
        object_name: The name of the object to get information about.

    Returns:
        A JSON string containing detailed information about the object.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("object.GetObjectInfo", {"name": object_name})
        return json.dumps(result)
    except Exception as e:
        return f"Error getting object info: {str(e)}"


@mcp.tool()
def create_object(
        ctx: Context,
        type: str = "Cube",
        name: str = None,
        location: List[float] = None,
        rotation: List[float] = None,
        scale: List[float] = None,
        color: List[float] = None,
        material: str = None
) -> str:
    """Create a new object in the Unity scene.

    Args:
        type: The type of object to create (Cube, Sphere, Cylinder, Plane, Capsule, Quad, Empty, Light, Camera).
        name: The name to give the new object. If not provided, the type will be used.
        location: The [x, y, z] coordinates for the object's position. Defaults to [0, 0, 0].
        rotation: The [x, y, z] Euler angles for the object's rotation. Defaults to [0, 0, 0].
        scale: The [x, y, z] scale factors for the object. Defaults to [1, 1, 1].
        color: The [r, g, b, a] color values (0.0-1.0) for the object's material. Alpha is optional.
        material: The name of a material to apply to the object.

    Returns:
        A JSON string with information about the created object.
    """
    try:
        connection = get_unity_connection()

        params = {
            "type": type,
            "name": name
        }

        if location:
            params["position"] = {
                "x": location[0],
                "y": location[1],
                "z": location[2]
            }

        if rotation:
            params["rotation"] = {
                "x": rotation[0],
                "y": rotation[1],
                "z": rotation[2]
            }

        if scale:
            params["scale"] = {
                "x": scale[0],
                "y": scale[1],
                "z": scale[2]
            }

        if color:
            params["color"] = color

        if material:
            params["material"] = material

        result = connection.send_command("object.CreatePrimitive", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error creating object: {str(e)}"


@mcp.tool()
def modify_object(
        ctx: Context,
        name: str,
        location: List[float] = None,
        rotation: List[float] = None,
        scale: List[float] = None,
        visible: bool = None
) -> str:
    """Modify an existing object in the Unity scene.

    Args:
        name: The name of the object to modify.
        location: The new [x, y, z] coordinates for the object's position.
        rotation: The new [x, y, z] Euler angles for the object's rotation.
        scale: The new [x, y, z] scale factors for the object.
        visible: Whether the object should be visible or not.

    Returns:
        A JSON string with information about the modified object.
    """
    try:
        connection = get_unity_connection()

        params = {
            "name": name
        }

        if location:
            params["position"] = {
                "x": location[0],
                "y": location[1],
                "z": location[2]
            }

        if rotation:
            params["rotation"] = {
                "x": rotation[0],
                "y": rotation[1],
                "z": rotation[2]
            }

        if scale:
            params["scale"] = {
                "x": scale[0],
                "y": scale[1],
                "z": scale[2]
            }

        if visible is not None:
            params["visible"] = visible

        result = connection.send_command("object.SetObjectTransform", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error modifying object: {str(e)}"


@mcp.tool()
def delete_object(ctx: Context, name: str) -> str:
    """Delete an object from the Unity scene.

    Args:
        name: The name of the object to delete.

    Returns:
        A JSON string confirming the deletion.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("object.DeleteObject", {"name": name})
        return json.dumps(result)
    except Exception as e:
        return f"Error deleting object: {str(e)}"


@mcp.tool()
def set_material(
        ctx: Context,
        object_name: str,
        material_name: str = None,
        color: List[float] = None
) -> str:
    """Apply or create a material for an object in the Unity scene.

    Args:
        object_name: The name of the object to apply the material to.
        material_name: The name of the material to apply or create.
        color: The [r, g, b, a] color values (0.0-1.0) for the material. Alpha is optional.

    Returns:
        A JSON string confirming the material application.
    """
    try:
        connection = get_unity_connection()

        params = {
            "objectName": object_name
        }

        if material_name:
            params["materialName"] = material_name

        if color:
            params["color"] = color

        result = connection.send_command("material.SetMaterial", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error setting material: {str(e)}"


@mcp.tool()
def create_material(
        ctx: Context,
        material_name: str = None,
        color: List[float] = None,
        shader: str = "Standard"
) -> str:
    """Apply or create a material for an object in the Unity scene.

    Args:
        material_name: The name of the material to apply or create.
        color: The [r, g, b, a] color values (0.0-1.0) for the material. Alpha is optional.
        shader: The name of the shader to use.

    Returns:
        A JSON string confirming the material application.
    """
    try:
        connection = get_unity_connection()

        params = {
            # "objectName": object_name
        }

        if material_name:
            params["name"] = material_name

        if color:
            params["color"] = color

        if shader:
            params["shader"] = shader

        result = connection.send_command("material.CreateMaterial", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error creating material: {str(e)}"


@mcp.tool()
def create_light(
        ctx: Context,
        type: str = "Point",
        name: str = None,
        location: List[float] = None,
        rotation: List[float] = None,
        color: List[float] = None,
        intensity: float = 1.0,
        range: float = 10.0
) -> str:
    """Create a light in the Unity scene.

    Args:
        type: The type of light (Point, Directional, Spot, Area).
        name: The name to give the light. If not provided, a default name will be used.
        location: The [x, y, z] coordinates for the light's position.
        rotation: The [x, y, z] Euler angles for the light's rotation.
        color: The [r, g, b] color values (0.0-1.0) for the light.
        intensity: The brightness of the light.
        range: The range of the light (for Point and Spot lights).

    Returns:
        A JSON string with information about the created light.
    """
    try:
        connection = get_unity_connection()

        params = {
            "type": type,
            "name": name,
            "intensity": intensity,
            "range": range
        }

        if location:
            params["position"] = {
                "x": location[0],
                "y": location[1],
                "z": location[2]
            }

        if rotation:
            params["rotation"] = {
                "x": rotation[0],
                "y": rotation[1],
                "z": rotation[2]
            }

        if color:
            params["color"] = color

        result = connection.send_command("lighting.CreateLight", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error creating light: {str(e)}"


@mcp.tool()
def create_camera(
        ctx: Context,
        name: str = None,
        location: List[float] = None,
        rotation: List[float] = None,
        field_of_view: float = 60.0,
        is_main: bool = False
) -> str:
    """Create a camera in the Unity scene.

    Args:
        name: The name to give the camera. If not provided, a default name will be used.
        location: The [x, y, z] coordinates for the camera's position.
        rotation: The [x, y, z] Euler angles for the camera's rotation.
        field_of_view: The field of view angle in degrees.
        is_main: Whether this camera should be set as the main camera.

    Returns:
        A JSON string with information about the created camera.
    """
    try:
        connection = get_unity_connection()

        params = {
            "name": name,
            "fieldOfView": field_of_view,
            "isMain": is_main
        }

        if location:
            params["position"] = {
                "x": location[0],
                "y": location[1],
                "z": location[2]
            }

        if rotation:
            params["rotation"] = {
                "x": rotation[0],
                "y": rotation[1],
                "z": rotation[2]
            }

        result = connection.send_command("camera.CreateCamera", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error creating camera: {str(e)}"


@mcp.tool()
def camera_look_at(
        ctx: Context,
        camera_name: str,
        target_name: str
) -> str:
    """Make a camera look at a specific object.

    Args:
        camera_name: The name of the camera.
        target_name: The name of the object to look at.

    Returns:
        A JSON string confirming the operation.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("camera.LookAt", {
            "cameraName": camera_name,
            "targetName": target_name
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error making camera look at target: {str(e)}"


@mcp.tool()
def instantiate_prefab(
        ctx: Context,
        prefab_path: str,
        name: str = None,
        location: List[float] = None,
        rotation: List[float] = None,
        scale: List[float] = None
) -> str:
    """Instantiate a prefab in the Unity scene.

    Args:
        prefab_path: The path to the prefab asset.
        name: The name to give the instantiated prefab. If not provided, the prefab name will be used.
        location: The [x, y, z] coordinates for the prefab's position.
        rotation: The [x, y, z] Euler angles for the prefab's rotation.
        scale: The [x, y, z] scale factors for the prefab.

    Returns:
        A JSON string with information about the instantiated prefab.
    """
    try:
        connection = get_unity_connection()

        params = {
            "prefabPath": prefab_path,
            "name": name
        }

        if location:
            params["position"] = {
                "x": location[0],
                "y": location[1],
                "z": location[2]
            }

        if rotation:
            params["rotation"] = {
                "x": rotation[0],
                "y": rotation[1],
                "z": rotation[2]
            }

        if scale:
            params["scale"] = {
                "x": scale[0],
                "y": scale[1],
                "z": scale[2]
            }

        result = connection.send_command("prefab.InstantiatePrefab", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error instantiating prefab: {str(e)}"


@mcp.tool()
def play_animation(
        ctx: Context,
        object_name: str,
        animation_name: str = None,
        crossfade_time: float = 0.3
) -> str:
    """Play an animation on an object.

    Args:
        object_name: The name of the object with the animation.
        animation_name: The name of the animation to play. If not provided, the default animation will be played.
        crossfade_time: The time to blend between animations.

    Returns:
        A JSON string confirming the animation playback.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("animation.PlayAnimation", {
            "objectName": object_name,
            "animationName": animation_name,
            "crossfadeTime": crossfade_time
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error playing animation: {str(e)}"


@mcp.tool()
def stop_animation(
        ctx: Context,
        object_name: str
) -> str:
    """Stop all animations on an object.

    Args:
        object_name: The name of the object with the animation.

    Returns:
        A JSON string confirming the animation stop.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("animation.StopAnimation", {
            "objectName": object_name
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error stopping animation: {str(e)}"


@mcp.tool()
def set_animation_parameter(
        ctx: Context,
        object_name: str,
        parameter_name: str,
        value: Union[int, float, bool, str]
) -> str:
    """Set a parameter on an Animator component.

    Args:
        object_name: The name of the object with the Animator.
        parameter_name: The name of the parameter to set.
        value: The value to set the parameter to (can be float, int, or bool).

    Returns:
        A JSON string confirming the parameter was set.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("animation.SetParameter", {
            "objectName": object_name,
            "parameterName": parameter_name,
            "value": value
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error setting animation parameter: {str(e)}"


@mcp.tool()
def create_animation_clip(
        ctx: Context,
        name: str,
        length: float = 1.0
) -> str:
    """Create a new animation clip.

    Args:
        name: The name of the animation clip.
        length: The length of the animation clip in seconds.

    Returns:
        A JSON string with information about the created animation clip.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("animation.CreateClip", {
            "name": name,
            "length": length
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error creating animation clip: {str(e)}"


@mcp.tool()
def add_animation_curve(
        ctx: Context,
        clip_name: str,
        object_name: str,
        property_path: str,
        keys: List[Dict[str, Any]]
) -> str:
    """Add an animation curve to an animation clip.

    Args:
        clip_name: The name of the animation clip.
        object_name: The name of the object to animate.
        property_path: The path to the property to animate (e.g., "localPosition.x").
        keys: A list of keyframes, each with "time" and "value" properties.

    Returns:
        A JSON string confirming the curve was added.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("animation.AddCurve", {
            "clipName": clip_name,
            "objectName": object_name,
            "propertyPath": property_path,
            "keys": keys
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error adding animation curve: {str(e)}"


@mcp.tool()
def set_global_lighting(
        ctx: Context,
        ambient_color: List[float] = None,
        ambient_intensity: float = None,
        fog_enabled: bool = None,
        fog_color: List[float] = None,
        fog_density: float = None
) -> str:
    """Set global lighting settings.

    Args:
        ambient_color: The [r, g, b] color values for ambient light.
        ambient_intensity: The intensity of ambient light.
        fog_enabled: Whether fog is enabled.
        fog_color: The [r, g, b] color values for fog.
        fog_density: The density of fog.

    Returns:
        A JSON string confirming the settings were applied.
    """
    try:
        connection = get_unity_connection()

        params = {}

        if ambient_color:
            params["ambientColor"] = ambient_color

        if ambient_intensity is not None:
            params["ambientIntensity"] = ambient_intensity

        if fog_enabled is not None:
            params["fogEnabled"] = fog_enabled

        if fog_color:
            params["fogColor"] = fog_color

        if fog_density is not None:
            params["fogDensity"] = fog_density

        result = connection.send_command("lighting.SetGlobalLighting", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error setting global lighting: {str(e)}"


@mcp.tool()
def modify_light(
        ctx: Context,
        light_name: str,
        color: List[float] = None,
        intensity: float = None,
        range: float = None,
        spot_angle: float = None,
        shadows_enabled: bool = None
) -> str:
    """Modify an existing light in the scene.

    Args:
        light_name: The name of the light to modify.
        color: The [r, g, b] color values for the light.
        intensity: The brightness of the light.
        range: The range of the light (for Point and Spot lights).
        spot_angle: The spot angle (for Spot lights).
        shadows_enabled: Whether the light casts shadows.

    Returns:
        A JSON string confirming the modifications.
    """
    try:
        connection = get_unity_connection()

        params = {
            "name": light_name
        }

        if color:
            params["color"] = color

        if intensity is not None:
            params["intensity"] = intensity

        if range is not None:
            params["range"] = range

        if spot_angle is not None:
            params["spotAngle"] = spot_angle

        if shadows_enabled is not None:
            params["shadowsEnabled"] = shadows_enabled

        result = connection.send_command("lighting.ModifyLight", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error modifying light: {str(e)}"


@mcp.tool()
def modify_camera(
        ctx: Context,
        camera_name: str,
        field_of_view: float = None,
        near_clip_plane: float = None,
        far_clip_plane: float = None,
        depth: int = None,
        is_main: bool = None
) -> str:
    """Modify an existing camera in the scene.

    Args:
        camera_name: The name of the camera to modify.
        field_of_view: The field of view angle in degrees.
        near_clip_plane: The near clip plane distance.
        far_clip_plane: The far clip plane distance.
        depth: The depth of the camera (determines rendering order).
        is_main: Whether this camera should be set as the main camera.

    Returns:
        A JSON string confirming the modifications.
    """
    try:
        connection = get_unity_connection()

        params = {
            "name": camera_name
        }

        if field_of_view is not None:
            params["fieldOfView"] = field_of_view

        if near_clip_plane is not None:
            params["nearClipPlane"] = near_clip_plane

        if far_clip_plane is not None:
            params["farClipPlane"] = far_clip_plane

        if depth is not None:
            params["depth"] = depth

        if is_main is not None:
            params["isMain"] = is_main

        result = connection.send_command("camera.ModifyCamera", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error modifying camera: {str(e)}"


@mcp.tool()
def search_asset_store(
        ctx: Context,
        query: str,
        category: str = None,
        max_results: int = 10
) -> str:
    """Search the Unity Asset Store.

    Args:
        query: The search query.
        category: The category to search in.
        max_results: The maximum number of results to return.

    Returns:
        A JSON string with search results.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("asset.SearchAssets", {
            "query": query,
            "category": category,
            "maxResults": max_results
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error searching asset store: {str(e)}"


@mcp.tool()
def download_asset(
        ctx: Context,
        asset_id: str,
        import_after_download: bool = True
) -> str:
    """Download an asset from the Unity Asset Store.

    Args:
        asset_id: The ID of the asset to download.
        import_after_download: Whether to import the asset after downloading.

    Returns:
        A JSON string with information about the downloaded asset.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("asset.DownloadAsset", {
            "assetId": asset_id,
            "importAfterDownload": import_after_download
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error downloading asset: {str(e)}"


@mcp.tool()
def add_animation_curve(
        ctx: Context,
        clip_name: str,
        object_name: str,
        property_path: str,
        keys: List[Dict[str, Any]]
) -> str:
    """Add an animation curve to an animation clip.

    Args:
        clip_name: The name of the animation clip.
        object_name: The name of the object to animate.
        property_path: The path to the property to animate (e.g., "localPosition.x").
        keys: A list of keyframes, each with "time" and "value" properties.

    Returns:
        A JSON string confirming the curve was added.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("animation.AddCurve", {
            "clipName": clip_name,
            "objectName": object_name,
            "propertyPath": property_path,
            "keys": keys
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error adding animation curve: {str(e)}"


@mcp.tool()
def set_global_lighting(
        ctx: Context,
        ambient_color: List[float] = None,
        ambient_intensity: float = None,
        fog_enabled: bool = None,
        fog_color: List[float] = None,
        fog_density: float = None
) -> str:
    """Set global lighting settings.

    Args:
        ambient_color: The [r, g, b] color values for ambient light.
        ambient_intensity: The intensity of ambient light.
        fog_enabled: Whether fog is enabled.
        fog_color: The [r, g, b] color values for fog.
        fog_density: The density of fog.

    Returns:
        A JSON string confirming the settings were applied.
    """
    try:
        connection = get_unity_connection()

        params = {}

        if ambient_color:
            params["ambientColor"] = ambient_color

        if ambient_intensity is not None:
            params["ambientIntensity"] = ambient_intensity

        if fog_enabled is not None:
            params["fogEnabled"] = fog_enabled

        if fog_color:
            params["fogColor"] = fog_color

        if fog_density is not None:
            params["fogDensity"] = fog_density

        result = connection.send_command("lighting.SetGlobalLighting", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error setting global lighting: {str(e)}"


@mcp.tool()
def modify_light(
        ctx: Context,
        light_name: str,
        color: List[float] = None,
        intensity: float = None,
        range: float = None,
        spot_angle: float = None,
        shadows_enabled: bool = None
) -> str:
    """Modify an existing light in the scene.

    Args:
        light_name: The name of the light to modify.
        color: The [r, g, b] color values for the light.
        intensity: The brightness of the light.
        range: The range of the light (for Point and Spot lights).
        spot_angle: The spot angle (for Spot lights).
        shadows_enabled: Whether the light casts shadows.

    Returns:
        A JSON string confirming the modifications.
    """
    try:
        connection = get_unity_connection()

        params = {
            "name": light_name
        }

        if color:
            params["color"] = color

        if intensity is not None:
            params["intensity"] = intensity

        if range is not None:
            params["range"] = range

        if spot_angle is not None:
            params["spotAngle"] = spot_angle

        if shadows_enabled is not None:
            params["shadowsEnabled"] = shadows_enabled

        result = connection.send_command("lighting.ModifyLight", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error modifying light: {str(e)}"


@mcp.tool()
def modify_camera(
        ctx: Context,
        camera_name: str,
        field_of_view: float = None,
        near_clip_plane: float = None,
        far_clip_plane: float = None,
        depth: int = None,
        is_main: bool = None
) -> str:
    """Modify an existing camera in the scene.

    Args:
        camera_name: The name of the camera to modify.
        field_of_view: The field of view angle in degrees.
        near_clip_plane: The near clip plane distance.
        far_clip_plane: The far clip plane distance.
        depth: The depth of the camera (determines rendering order).
        is_main: Whether this camera should be set as the main camera.

    Returns:
        A JSON string confirming the modifications.
    """
    try:
        connection = get_unity_connection()

        params = {
            "name": camera_name
        }

        if field_of_view is not None:
            params["fieldOfView"] = field_of_view

        if near_clip_plane is not None:
            params["nearClipPlane"] = near_clip_plane

        if far_clip_plane is not None:
            params["farClipPlane"] = far_clip_plane

        if depth is not None:
            params["depth"] = depth

        if is_main is not None:
            params["isMain"] = is_main

        result = connection.send_command("camera.ModifyCamera", params)
        return json.dumps(result)
    except Exception as e:
        return f"Error modifying camera: {str(e)}"


@mcp.tool()
def search_asset_store(
        ctx: Context,
        query: str,
        category: str = None,
        max_results: int = 10
) -> str:
    """Search the Unity Asset Store.

    Args:
        query: The search query.
        category: The category to search in.
        max_results: The maximum number of results to return.

    Returns:
        A JSON string with search results.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("asset.SearchAssets", {
            "query": query,
            "category": category,
            "maxResults": max_results
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error searching asset store: {str(e)}"


@mcp.tool()
def download_asset(
        ctx: Context,
        asset_id: str,
        import_after_download: bool = True
) -> str:
    """Download an asset from the Unity Asset Store.

    Args:
        asset_id: The ID of the asset to download.
        import_after_download: Whether to import the asset after downloading.

    Returns:
        A JSON string with information about the downloaded asset.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("asset.DownloadAsset", {
            "assetId": asset_id,
            "importAfterDownload": import_after_download
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error downloading asset: {str(e)}"


@mcp.tool()
def get_asset_categories(ctx: Context) -> str:
    """Get a list of asset categories from the Unity Asset Store.

    Returns:
        A JSON string with a list of asset categories.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("asset.GetCategories")
        return json.dumps(result)
    except Exception as e:
        return f"Error getting asset categories: {str(e)}"


@mcp.tool()
def get_assistant_insights(
        ctx: Context,
        scene_name: str = None
) -> str:
    """Get AI assistant insights about the current scene.

    Args:
        scene_name: The name of the scene to analyze. If not provided, the current scene will be used.

    Returns:
        A JSON string with insights about the scene.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("assistant.GetInsights", {
            "sceneName": scene_name
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error getting assistant insights: {str(e)}"


@mcp.tool()
def get_creative_suggestions(
        ctx: Context,
        object_name: str = None,
        suggestion_type: str = "general"
) -> str:
    """Get creative suggestions from the AI assistant.

    Args:
        object_name: The name of the object to get suggestions for. If not provided, general suggestions will be given.
        suggestion_type: The type of suggestions to get (general, visual, gameplay, story).

    Returns:
        A JSON string with creative suggestions.
    """
    try:
        connection = get_unity_connection()
        result = connection.send_command("assistant.GetSuggestions", {
            "objectName": object_name,
            "suggestionType": suggestion_type
        })
        return json.dumps(result)
    except Exception as e:
        return f"Error getting creative suggestions: {str(e)}"


@mcp.prompt()
def unity_assistant_guide() -> str:
    """Provides guidance on using the AI assistant features in Unity."""
    return """
    # Unity MCP Assistant Guide

    The Unity MCP system includes an AI assistant that can help you with your game development process. Here's how to use it:

    ## Getting Insights

    Use the `get_assistant_insights` tool to analyze your scene and get AI-powered insights:

    - Scene composition analysis
    - Performance optimization suggestions
    - Visual balance and aesthetic feedback
    - Potential issues and how to fix them

    ## Creative Suggestions

    Use the `get_creative_suggestions` tool to get ideas for your game:

    - General suggestions for improving your scene
    - Visual suggestions for enhancing the look and feel
    - Gameplay suggestions for making your game more engaging
    - Story suggestions for developing your narrative

    ## Working with the Assistant

    1. Start by getting insights about your current scene
    2. Ask for specific suggestions based on the insights
    3. Implement the suggestions using the other Unity MCP tools
    4. Get new insights to see how your changes have improved the scene

    The assistant works best when you provide specific context about what you're trying to achieve. For example, instead of asking for general suggestions, ask for suggestions about a specific object or aspect of your game.

    Remember that the assistant is a tool to enhance your creativity, not replace it. Use its suggestions as inspiration for your own ideas.
    """


def main():
    """Main entry point for the Unity MCP server."""
    parser = argparse.ArgumentParser(description="Unity MCP Server")
    parser.add_argument("--host", default="localhost", help="Host to bind the server to") # 暂时没用到
    parser.add_argument("--port", type=int, default=8000, help="Port to bind the server to") # 暂时没用到
    parser.add_argument("--unity-host", default="localhost", help="Unity host to connect to") # 与unity编辑器通信的主机地址
    parser.add_argument("--unity-port", type=int, default=9876, help="Unity port to connect to") # 与unity编辑器通信的端口，在unity脚本中设置同一个端口号。
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    # Set the global connection parameters
    global _unity_connection
    _unity_connection = UnityConnection(host=args.unity_host, port=args.unity_port)

    # Run the server
    mcp.run()


if __name__ == "__main__":
    main()