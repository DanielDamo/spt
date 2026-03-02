#!/usr/bin/env python3
"""
Headless BlueZ GATT server (DBus) with:
- Custom GATT service + characteristics (CMD write, RESP read)
- LE advertisement broadcasting the service UUID
- Headless pairing agent that auto-accepts "Just Works" pairing so the Pi never needs a screen/keyboard
- Single-connection enforcement: only one device connected at a time
"""

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib
from helpers import State

# ----------------------------
# BlueZ / DBus interface names
# ----------------------------
BLUEZ_SERVICE_NAME = "org.bluez"

# Managers exposed on the adapter object (e.g. /org/bluez/hci0)
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"

# Standard DBus helper interfaces
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"   # for GetManagedObjects()
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"    # for Get/Set/PropertiesChanged signals

# BlueZ object interfaces
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"

# Pairing agent interfaces
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
AGENT_IFACE = "org.bluez.Agent1"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"

# DBus object paths
APP_PATH = "/com/spt/app"
ADV_PATH = "/com/spt/adv"
AGENT_PATH = "/com/spt/agent"

# UUIDs
SERVICE_UUID = "12345678-1234-5678-1234-56789abc0000"
CMD_UUID     = "12345678-1234-5678-1234-56789abc0001"  # write commands
RESP_UUID    = "12345678-1234-5678-1234-56789abc0002"  # read responses


def get_adapter_path(bus: dbus.SystemBus) -> str:
    """
    Find the first adapter that supports both:
    - LE Advertising
    - GATT application registration

    BlueZ exposes objects under '/' via ObjectManager. Scan for an object
    that has both manager interfaces present (usually /org/bluez/hci0).
    """
    obj_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
    objects = obj_manager.GetManagedObjects()

    for path, ifaces in objects.items():
        if LE_ADVERTISING_MANAGER_IFACE in ifaces and GATT_MANAGER_IFACE in ifaces:
            return path

    raise RuntimeError("No BLE adapter with GATT+Advertising found")


# -------------------------------------------------------
# DBus ObjectManager implementation for GATT
# -------------------------------------------------------
class Application(dbus.service.Object):
    """
    A BlueZ application is an ObjectManager that exposes:
    - Services (GattService1)
    - Characteristics (GattCharacteristic1)
    - (Optionally) Descriptors

    BlueZ calls GetManagedObjects() once during RegisterApplication
    to discover the full tree of objects and their properties.
    """
    def __init__(self, bus):
        super().__init__(bus, APP_PATH)
        self.services = []

    def get_path(self):
        return dbus.ObjectPath(APP_PATH)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        """
        Return a dict:
          { object_path: { interface_name: { prop_name: value, ... }, ... }, ... }

        BlueZ uses this to learn about our services/characteristics.
        """
        response = {}
        for svc in self.services:
            response[svc.get_path()] = svc.get_properties()
            for ch in svc.characteristics:
                response[ch.get_path()] = ch.get_properties()
        return response


class Service(dbus.service.Object):
    """
    Implements org.bluez.GattService1
    """
    def __init__(self, bus, index, uuid, primary=True):
        # Each service needs its own unique object path
        self.path = f"{APP_PATH}/service{index}"
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, ch):
        self.characteristics.append(ch)

    def get_properties(self):
        """
        Properties for GattService1.
        - UUID: service uuid
        - Primary: whether primary service
        - Characteristics: array of characteristic object paths
        """
        return {
            GATT_SERVICE_IFACE: {
                "UUID": self.uuid,
                "Primary": dbus.Boolean(self.primary),
                "Characteristics": dbus.Array([c.get_path() for c in self.characteristics], signature="o"),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        """
        BlueZ may call GetAll() on Properties interface to fetch all properties
        for a given interface. Support only the service interface here.
        """
        if interface != GATT_SERVICE_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")
        return self.get_properties()[GATT_SERVICE_IFACE]


class Characteristic(dbus.service.Object):
    """
    Implements org.bluez.GattCharacteristic1.

    Subclasses override ReadValue/WriteValue to implement behavior.
    """
    def __init__(self, bus, index, uuid, flags, service):
        # Each characteristic needs its own unique object path under the service
        self.path = service.path + f"/char{index}"
        self.bus = bus
        self.uuid = uuid
        self.flags = flags     # e.g. ["read"], ["write"], ["encrypt-read"], ...
        self.service = service
        self.value = dbus.Array([], signature="y")  # characteristic value as bytes
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        """
        Properties for GattCharacteristic1.
        - UUID
        - Service path (parent)
        - Flags: read/write/notify + encryption requirements etc.
        """
        return {
            GATT_CHRC_IFACE: {
                "UUID": self.uuid,
                "Service": self.service.get_path(),
                "Flags": dbus.Array(self.flags, signature="s"),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        """
        Properties.GetAll handler for GattCharacteristic1.
        """
        if interface != GATT_CHRC_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")
        return self.get_properties()[GATT_CHRC_IFACE]

    # ---- Default handlers (override in subclasses) ----
    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        """
        Called by BlueZ when a client performs a GATT read.
        'options' contains things like offset, device, link type.
        """
        return self.value

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        """
        Called by BlueZ when a client performs a GATT write.
        'value' is an array of bytes. Save it as our characteristic value by default.
        """
        self.value = dbus.Array(value, signature="y")


class Advertisement(dbus.service.Object):
    """
    Implements org.bluez.LEAdvertisement1.

    This is what makes the device appear in BLE scans with:
    - LocalName
    - ServiceUUIDs
    - Tx power (optional)
    """
    def __init__(self, bus, local_name, service_uuids):
        self.path = ADV_PATH
        self.bus = bus
        self.local_name = local_name
        self.service_uuids = service_uuids
        self.type = "peripheral"  # typical for GATT server device
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        """
        LEAdvertisement1 properties:
        - Type: "peripheral" or "broadcast"
        - ServiceUUIDs: list of advertised service UUIDs (helps clients filter)
        - LocalName: device name
        - IncludeTxPower: include TX power in advertisement payload
        """
        return {
            LE_ADVERTISEMENT_IFACE: {
                "Type": self.type,
                "ServiceUUIDs": dbus.Array(self.service_uuids, signature="s"),
                "LocalName": self.local_name,
                "IncludeTxPower": dbus.Boolean(True),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        """
        Properties.GetAll handler for LEAdvertisement1.
        """
        if interface != LE_ADVERTISEMENT_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        """
        Called by BlueZ when the advertisement is released/unregistered.
        """
        print("[BLE] Advertisement released")


# ----------------------------------------
# Headless pairing agent: auto-accept
# ----------------------------------------
class Rejected(dbus.DBusException):
    """
    BlueZ expects this DBus error name when rejecting a pairing request.
    """
    _dbus_error_name = "org.bluez.Error.Rejected"


class Agent(dbus.service.Object):
    """
    Implements org.bluez.Agent1.

    Register this agent with capability "NoInputNoOutput" (Just Works).
    That means:
    - Cannot show/enter a PIN/passkey
    - Can auto-confirm & authorize pairing

    Result: The Pi does NOT need a UI prompt to accept pairing.
    """
    def __init__(self, bus):
        super().__init__(bus, AGENT_PATH)
        self.bus = bus

    def _trust(self, device_path: str) -> None:
        """
        Mark a device Trusted. Trusted devices can reconnect/pair without prompts.
        """
        props = dbus.Interface(self.bus.get_object(BLUEZ_SERVICE_NAME, device_path), DBUS_PROP_IFACE)
        props.Set(DEVICE_IFACE, "Trusted", dbus.Boolean(True))

    @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        print("[BLE] Agent released")

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        """
        Called when BlueZ asks if a device should be authorized.
        Auto-authorize and trust it.
        """
        print("[BLE] RequestAuthorization", device)
        self._trust(device)

    @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        """
        Called when a device requests authorization for a specific service UUID.
        Allow it.
        """
        print("[BLE] AuthorizeService", device, uuid)
        self._trust(device)

    @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        """
        Called for passkey confirmation (Just Works confirmation stage).
        Auto-confirm and trust.
        """
        print("[BLE] RequestConfirmation", device, passkey)
        self._trust(device)

    # For NoInputNoOutput, reject anything requiring user input:
    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        raise Rejected("No input")

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        raise Rejected("No input")


# -------------------------
# Command handler
# -------------------------
def handle_command(cmd: str, pi) -> str:
    """
    Parse and pass on commands to the pi
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return "ERR empty"

    parts = cmd.split()
    op = parts[0].lower()

    pi._cmd_q.put(cmd)

    return f"{cmd} queued"


# -----------------------------
# Characteristics for GATT
# -----------------------------
class CmdCharacteristic(Characteristic):
    """
    CMD characteristic:
    - write + encrypt-write means the client must pair/encrypt before writing
    - Interpret the written bytes as a UTF-8 command string
    - Write the response into RESP characteristic value
    """
    def __init__(self, bus, index, service, pi, resp_char):
        super().__init__(bus, index, CMD_UUID, ["write", "encrypt-write"], service)
        self.pi = pi
        self.resp_char = resp_char

    def WriteValue(self, value, options):
        # Convert bytes -> text command
        cmd = bytes(value).decode("utf-8", errors="replace")

        # Execute command and create response string
        resp = handle_command(cmd, self.pi)

        # Store response bytes for the next read of RESP characteristic
        self.resp_char.value = dbus.Array(resp.encode("utf-8"), signature="y")

        print(f"[BLE] CMD: {cmd!r} -> {resp!r}")


        if getattr(self.resp_char, "notifying", False):
            self.resp_char.PropertiesChanged(
                GATT_CHRC_IFACE,
                {"Value": self.resp_char.value},
                []
            )


class RespCharacteristic(Characteristic):
    """
    RESP characteristic:
    - read + encrypt-read means the client must pair/encrypt before reading
    - Its value is updated by the CMD characteristic after a command executes
    """
    def __init__(self, bus, index, service):
        super().__init__(bus, index, RESP_UUID, ["read", "encrypt-read", "notify"], service)
        self.value = dbus.Array(b"OK ready", signature="y")
        self.notifying = False
    
    def StartNotify(self):
        print("[BLE] Notifications enabled")
        self.notifying = True

    def StopNotify(self):
        print("[BLE] Notifications disabled")
        self.notifying = False

# --------------------------
# Main server entry point
# --------------------------
def run(pi_controller, name="SPT-Pi"):
    """
    Start:
    - DBus main loop
    - Headless pairing agent
    - GATT application
    - LE advertisement
    - Single-connection enforcement
    """
    # Make DBus integrate with GLib main loop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    # Find adapter path (e.g., /org/bluez/hci0)
    adapter_path = get_adapter_path(bus)
    print("[BLE] Using adapter:", adapter_path)

    # Create the GLib loop now so callbacks can call mainloop.quit()
    mainloop = GLib.MainLoop()

    # Ensure adapter is on + pairable/discoverable so phones can find & pair
    adapter_props = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter_path), DBUS_PROP_IFACE)
    adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
    adapter_props.Set(ADAPTER_IFACE, "Pairable", dbus.Boolean(True))
    adapter_props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(True))

    # Register headless agent so pairing doesn't require acceptance on the Pi
    agent = Agent(bus)
    agent_mgr = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, "/org/bluez"), AGENT_MANAGER_IFACE)
    agent_mgr.RegisterAgent(AGENT_PATH, "NoInputNoOutput")   # Just Works
    agent_mgr.RequestDefaultAgent(AGENT_PATH)                # make it the active default
    print("[BLE] Headless agent registered (NoInputNoOutput)")

    # Build GATT app (service + characteristics)
    app = Application(bus)
    svc = Service(bus, 0, SERVICE_UUID, primary=True)

    resp = RespCharacteristic(bus, 0, svc)
    cmd  = CmdCharacteristic(bus, 1, svc, pi_controller, resp)

    svc.add_characteristic(resp)
    svc.add_characteristic(cmd)
    app.add_service(svc)

    # Get the BlueZ managers from the adapter object
    service_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter_path), GATT_MANAGER_IFACE)
    ad_manager      = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter_path), LE_ADVERTISING_MANAGER_IFACE)

    # Create advertisement object
    adv = Advertisement(bus, local_name=name, service_uuids=[SERVICE_UUID])

    # ------------------------------------------------------
    # Single-connection enforcement state (simple approach)
    # ------------------------------------------------------
    # Track the DBus object path for the single connected device.
    connected_device_path = {"path": None}

    # Track whether advertising is currently active, so no double-registering.
    advertising_active = {"active": False}

    def start_advertising():
        """
        Register our LE advertisement with BlueZ.
        This makes the device show up in BLE scan results.
        """
        if advertising_active["active"]:
            return

        pi_controller.set_state(State.BLUETOOTH_NOT_CONNECTED)

        ad_manager.RegisterAdvertisement(
            adv.get_path(),
            {},
            reply_handler=lambda: print("[BLE] Advertisement registered (scan for it)"),
            error_handler=lambda e: print("[BLE] Failed to register advertisement:", e),
        )
        advertising_active["active"] = True

    def stop_advertising():
        """
        Unregister the advertisement.
        Helpful for enforcing single connection in practice: if we stop advertising
        once someone connects, fewer devices attempt to connect simultaneously.
        """
        if not advertising_active["active"]:
            return
        pi_controller.set_state(State.BLUETOOTH_CONNECTING)
        try:
            ad_manager.UnregisterAdvertisement(adv.get_path())
        except Exception:
            pass
        advertising_active["active"] = False
        pi_controller.set_state(State.BLUETOOTH_CONNECTED)
        print("[BLE] Advertisement stopped")

    def disconnect_device(dev_path: str):
        """
        Force-disconnect a device via org.bluez.Device1.Disconnect().
        Used to kick off "extra" devices when one is already connected.
        """
        try:
            dev = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, dev_path), DEVICE_IFACE)
            dev.Disconnect()
            print("[BLE] Disconnected extra device:", dev_path)
        except Exception as e:
            print("[BLE] Disconnect failed:", e)

    def disconnect_all_devices():
        """
        Force-disconnect all devices, used to reset bluetooth
        """
        obj_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
        objects = obj_manager.GetManagedObjects()
        for path, ifaces in objects.items():
            if DEVICE_IFACE not in ifaces:
                continue
            dev_props = ifaces[DEVICE_IFACE]
            try:
                if bool(dev_props.get("Connected", False)):
                    disconnect_device(path)
            except Exception as e:
                print("[BLE] Failed disconnect", path, e)

    def ble_reset_now():
        print("[BLE] Reset requested: disconnect all + restart advertising")
        # Stop advertising briefly (optional but helps)
        stop_advertising()

        # Disconnect anyone connected
        disconnect_all_devices()

        # Clear our "current connection" tracking too
        connected_device_path["path"] = None

        # Start advertising again
        start_advertising()

        # Returning False makes GLib.idle_add run once
        return False
    
    def ble_stop_now():
        print("[BLE] Stop requested")
        # Unsubscribe everyone
        try:
            resp.StopNotify()
        except Exception:
            pass

        # Disconnect everyone
        try:
            disconnect_all_devices()
        except Exception:
            pass

        # Stop advertising (safe even if already stopped)
        try:
            stop_advertising()
        except Exception:
            pass

        # Unregister GATT application
        try:
            service_manager.UnregisterApplication(app.get_path())
        except Exception:
            pass

        # Unregister agent
        try:
            agent_mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass

        # Quit the GLib loop -> run() returns
        try:
            mainloop.quit()
        except Exception:
            pass

        return False

    def on_properties_changed(interface, changed, invalidated, path=None):
        """
        Handler for PropertiesChanged signals.
        Listen for org.bluez.Device1 changes to detect when devices connect/disconnect.

        - When first device connects: record it + stop advertising.
        - If another device connects while one already connected: disconnect the newcomer.
        - When the connected device disconnects: clear it + restart advertising.
        """
        if interface != DEVICE_IFACE:
            return
        if "Connected" not in changed:
            return

        is_connected = bool(changed["Connected"])

        if is_connected:
            # After device connects
            if connected_device_path["path"] is None:
                connected_device_path["path"] = path
                print("[BLE] Device connected:", path)
                stop_advertising()

                # Enable notify if not already
                if not resp.notifying:
                    resp.StartNotify()

                # Update the characteristic value
                config_str = pi_controller.get_config()
                resp.value = dbus.Array(config_str.encode("utf-8"), signature="y")

                # Trigger a notification so the app gets it
                resp.PropertiesChanged(
                    GATT_CHRC_IFACE,
                    {"Value": resp.value},
                    []
                )

                print("[BLE] Sent initial config to app:", config_str)

            else:
                # Already have a connected device; reject any additional one
                if path != connected_device_path["path"]:
                    print("[BLE] Extra device tried to connect, rejecting:", path)
                    disconnect_device(path)

        else:
            # If the known connected device disconnected, reopen to new connections
            if connected_device_path["path"] == path:
                print("[BLE] Device disconnected:", path)
                connected_device_path["path"] = None
                start_advertising()

    # Subscribe to PropertiesChanged signals system-wide.
    # The DBus wrapper passes the object path via path_keyword.
    bus.add_signal_receiver(
        on_properties_changed,
        dbus_interface=DBUS_PROP_IFACE,
        signal_name="PropertiesChanged",
        path_keyword="path",
    )

    # -----------------------
    # Register GATT app
    # -----------------------
    def reg_app_cb():
        print("[BLE] GATT app registered")

    def reg_app_err(e):
        print("[BLE] Failed to register GATT app:", e)
        mainloop.quit()

    service_manager.RegisterApplication(
        app.get_path(),
        {},
        reply_handler=reg_app_cb,
        error_handler=reg_app_err,
    )

    # Allow pi to access the reset function
    pi_controller.ble_reset = lambda: GLib.idle_add(ble_reset_now)
    # Allow pi to access stop function
    pi_controller.ble_stop = lambda: GLib.idle_add(ble_stop_now)

    # Start advertising so phones can find us
    start_advertising()

    # -----------------------
    # Run main loop
    # -----------------------
    try:
        mainloop.run()
    finally:
        # Cleanup: unregister advertisement, application, agent
        try:
            stop_advertising()
        except Exception:
            pass
        try:
            service_manager.UnregisterApplication(app.get_path())
        except Exception:
            pass
        try:
            agent_mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass


if __name__ == "__main__":
    print("Run this from main.py.")