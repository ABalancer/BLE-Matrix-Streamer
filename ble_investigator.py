import asyncio
from bleak import BleakScanner
from bleak import BleakClient
import threading
import struct


'''
TODO: 
    Test multiple access properties
    Alter the program to be interacted with in a terminal
'''


async def read(bleak_client, characteristic_uuid):
    ble_data = await bleak_client.read_gatt_char(characteristic_uuid)
    print("\nRead: {}\n".format(ble_data))
    decoding_mode = input("Decode Type ( none / utf-8 / uint8_t / uint16_t / custom ) : ")
    decoded_data = decode_data(ble_data, decoding_mode)
    if "bytearray" not in str(decoded_data):
        print("Decoded Data: {}\n".format(decoded_data))


async def notify(bleak_client, characteristic_uuid):
    decoding_mode = input("Decode Type ( none / utf-8 / uint8_t / uint16_t / custom ) : ")
    unpacking_options = None
    if decoding_mode == "custom":
        unpacking_options = input("Set parameters to be used with struct.unpack(): ")

    await bleak_client.start_notify(characteristic_uuid,
                                    decode_notification_handler(decoding_mode, unpacking_options))
    print("Listening for notifications. Press any key to stop...")
    await nonblocking_wait_for_input()
    await bleak_client.stop_notify(characteristic_uuid)
    print("Unsubscribed from characteristic")


async def write(bleak_client, characteristic_uuid):
    byte_string = input("Input byte string of structure 0xXX, 0xXX, ... , 0xXX : ")
    byte_strings = [b.strip() for b in byte_string.split(",")]
    byte_values = []
    not_byte_flag = False
    for byte_string in byte_strings:
        byte = is_byte(byte_string)
        if byte:
            byte_values.append(int(byte_string, 16))
        else:
            not_byte_flag = True
    if not not_byte_flag:
        await bleak_client.write_gatt_char(characteristic_uuid, bytearray(byte_values))
        print("Write complete")
    else:
        print("Byte string was in an incorrect format")


def get_characteristic_access_choice(characteristic_info):
    characteristic_uuid = None
    access_type = None
    characteristic_choice = input("Access Characteristic Number: ")
    if characteristic_choice.isnumeric():
        characteristic_number = int(characteristic_choice)
        characteristic_uuid = characteristic_info[characteristic_number][0]
        access_types = characteristic_info[characteristic_number][1]
        if len(access_types) <= 0:
            print("Unknown Access Type")
        elif len(access_types) == 1:
            access_type = access_types[0]
        else:
            access_types_str = ""
            for access_type in access_types:
                if access_types.index(access_type) > 0:
                    access_types_str += " / "
                access_types_str += access_type

            access_type = input("Choose from available access types ( {} ) : ".format(access_types_str))
    return access_type, characteristic_uuid


async def nonblocking_wait_for_input():
    # Run input in another thread to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def wait_for_enter():
        input()
        # noinspection PyTypeChecker
        loop.call_soon_threadsafe(stop_event.set)

    threading.Thread(target=wait_for_enter, daemon=True).start()
    await stop_event.wait()


def is_byte(text):
    try:
        value = int(text, 16)
        return 0 <= value <= 0xFF
    except ValueError:
        return False


def decode_notification_handler(decoding_mode=None, custom_unpacking_options=None):
    def notification_handler(sender, data):
        if decoding_mode is not None:
            data = decode_data(data, decoding_mode, custom_unpacking_options)
        print("{}: {}".format("Notification", data))
    return notification_handler


def print_device_list(ble_devices_array):
    print("-{:-^10}-{:-^26}-{:-^21}-".format("", "", ""))
    print("|{:^10}|{:^26}|{:^21}|".format("Number", "Name", "UUID"))
    print("-{:-^10}-{:-^26}-{:-^21}-".format("", "", ""))
    for ble_device in ble_devices_array:
        print("|{:^10}|{:^26}|{:^21}|".format(ble_devices_array.index(ble_device), ble_device[0], ble_device[1]))
    print("-{:-^10}-{:-^26}-{:-^21}-".format("", "", ""))


def strip_tuple(tuple_data):
    if len(tuple_data) > 1:
        str_data = str(tuple_data).strip("()")
    else:
        str_data = str(tuple_data).strip("(),")
    return str_data


def decode_data(coded_data, decoding_mode, passing_unpacking_options=None):
    if decoding_mode == "utf-8":
        decoded_data = coded_data.decode("utf-8")
    elif decoding_mode == "uint8_t":
        unpacked_data = struct.unpack('<' + 'B' * len(coded_data), coded_data)
        decoded_data = strip_tuple(unpacked_data)
    elif decoding_mode == "uint16_t":
        unpacked_data = struct.unpack('<' + 'H' * (len(coded_data) // 2), coded_data)
        decoded_data = strip_tuple(unpacked_data)
    elif decoding_mode == "custom":
        if passing_unpacking_options is None:
            unpacking_options = input("Set parameters to be used with struct.unpack(): ")
        else:
            unpacking_options = passing_unpacking_options
        try:
            unpacked_data = struct.unpack(unpacking_options, coded_data)
            decoded_data = strip_tuple(unpacked_data)
        except Exception as e:
            print("Invalid format setting that results in error: {}".format(e))
            return coded_data
    else:
        return coded_data
    return decoded_data


async def connect(device_address, characteristic_info):
    print("Connecting to device...")
    async with (BleakClient(device_address) as bleak_client):
        print("Connected to device")
        while bleak_client.is_connected:
            access_type, characteristic_uuid = get_characteristic_access_choice(characteristic_info)

            if access_type == "read":
                await read(bleak_client, characteristic_uuid)

            elif access_type == "notify" or access_type == "indicate":
                await notify(bleak_client, characteristic_uuid)

            elif access_type == "write":
                await write(bleak_client, characteristic_uuid)
            else:
                print("Invalid characteristic choice")

            await asyncio.sleep(0.1)
            disconnect_option = input("Disconnect from device? ( y / n ) : ")
            if disconnect_option == "y":
                await disconnect(bleak_client)


async def disconnect(bleak_client):
    if bleak_client.is_connected:
        await bleak_client.disconnect()
        print("Disconnected from device")


async def device_scanner():
    try:
        print("Scanning devices...")
        ble_devices = await BleakScanner.discover()
        if ble_devices:
            ble_devices_array = []
            number = 0
            for ble_device in ble_devices:
                if ble_device.name:
                    device_name = ble_device.name
                else:
                    device_name = "Unnamed"
                ble_devices_array.append((device_name, ble_device.address))
                number += 1
            print_device_list(ble_devices_array)
            return ble_devices_array

    except Exception as e:
        print("Bleak Scanner Failed - Possible Issue with Bluetooth Adapter")
        exit("Error: {}".format(e))


async def find_characteristics(device_address):
    bleak_client = BleakClient(device_address)
    print("Connecting to device...")
    try:
        await bleak_client.connect()
        print("Connected to device")

        print("-{:-^10}-{:-^18}-{:-^40}-{:-^29}-{:-^80}-".format("", "", "", "", ""))
        print("|{:^10}|{:^18}|{:^40}|{:^29}|{:^80}|"
              .format("Number", "Type", "UUID", "Description / Access", "Value"))
        characteristic_info = []
        number = 0
        for service in bleak_client.services:
            print("-{:-^10}-{:-^18}-{:-^40}-{:-^29}-{:-^80}-".format("", "", "", "", ""))
            print("|{:^10}|{:^18}|{:^40}|{:^29}|{:^80}|"
                  .format("", "Service", service.uuid, service.description, ""))
            for char in service.characteristics:
                char_properties_text = ", ".join(char.properties)
                value = ""
                if "read" in char.properties:
                    try:
                        value = await bleak_client.read_gatt_char(char.uuid)
                        stripped = (str(value).replace("bytearray", "")
                                              .replace("(", "").replace(")", ""))
                        if "\\x" in str(value):
                            value = stripped
                        elif stripped.isascii():
                            value = value.decode('utf-8')
                    except Exception as e:
                        value = "Read failed: {}".format(e)
                characteristic_info.append((char.uuid, char.properties))
                print("|{:^10}|{:^18}|{:^40}|{:^29}|{:^80}|"
                      .format(number, "Characteristic", char.uuid, char_properties_text, value))
                number += 1
        print("-{:-^10}-{:-^18}-{:-^40}-{:-^29}-{:-^80}-".format("", "", "", "", ""))
        await disconnect(bleak_client)
        return characteristic_info

    except Exception as e:
        print("Connection terminated on BLE Device")
        exit("Error: {}".format(e))


if __name__ == "__main__":
    characteristic = "FF:EE:DD:CC:BB:AA"
    devices = asyncio.run(device_scanner())
    selected_device = input("Connect to Device Number: ")
    if selected_device.isdigit():
        selected_device = int(selected_device)
        if len(devices) > selected_device >= 0:
            address = devices[selected_device][1]
            characteristics = asyncio.run(find_characteristics(address))
            access = input("Interact with device? ( y / n ) : ")
            if access == "y":
                asyncio.run(connect(address, characteristics))
            else:
                print("Program Exiting...")
    else:
        print("Invalid Input. Program Exiting...")
