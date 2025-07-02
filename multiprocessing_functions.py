from bleak import BleakScanner
from bleak import BleakClient
import asyncio
import multiprocessing
import struct

# Deprecated


def decode_matrix_data(num_rows, num_cols, byte_array):
    # Assuming each element is a 12-bit unsigned integer (2 bytes)
    element_size = 2

    # Unpack the byte array into a flat list of integers
    unpacked_matrix_data = struct.unpack('<' + 'H' * (len(byte_array) // element_size), byte_array)
    # Reshape the flat list into a 2D matrix
    matrix_data = [unpacked_matrix_data[i:i+num_rows] for i in range(0, len(unpacked_matrix_data), num_cols)]

    return matrix_data


def decode_matrix_dimensions(byte_array):
    num_of_rows, num_of_cols = struct.unpack('<BB', byte_array)
    return num_of_cols, num_of_rows


def start_asyncio(target, args):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if isinstance(args, tuple) and len(args) > 0:
        loop.run_until_complete(target(*args))
    else:
        loop.run_until_complete(target(args))
    loop.close()


def process_handler(target, args):
    process = multiprocessing.Process(target=start_asyncio, args=(target, args))
    process.start()
    return process


# Function to update the listbox with detected devices
async def device_scanner(queue, lock, data_availability, desired_service_uuid):
    try:
        devices = await BleakScanner(service_uuids=desired_service_uuid).discover()
        if devices:
            devices_2d = []
            for device in devices:
                if device.name:
                    device_name = device.name
                else:
                    device_name = "None"
                devices_2d.append((device.address, device_name))
            lock.acquire()
            queue.put(devices_2d)
            data_availability.value = 1
            lock.release()
            print("Devices Found")
    except Exception as e:
        print("Bleak Scanner Failed - Possible Issue with Bluetooth Adapter")
        print("Error: {}".format(e))
        data_availability.value = 2


async def connect(queue, lock, connected, data_availability, device_address,
                  dimensions_characteristic, data_characteristic):
    client = BleakClient(device_address)
    try:
        await client.connect()
        print("Connected to the device")
        lock.acquire()
        connected.value = 1
        lock.release()
        matrix_dimensions = await client.read_gatt_char(dimensions_characteristic)
        number_of_rows, number_of_columns = decode_matrix_dimensions(matrix_dimensions)
        lock.acquire()
        queue.put(matrix_dimensions)
        data_availability.value = 1
        lock.release()
        while connected.value:
            byte_array = await client.read_gatt_char(data_characteristic)
            matrix_data = decode_matrix_data(number_of_rows, number_of_columns, byte_array)
            print(matrix_data)
            matrix_data = [[0 for _ in range(number_of_columns)] for _ in range(number_of_rows)]
            lock.acquire()
            queue.put(matrix_data)
            data_availability.value = 1
            lock.release()

    except Exception as e:
        lock.acquire()
        queue.put(None)
        data_availability.value = 0
        lock.release()
        print("Connection terminated on BLE Device")
        print("Error: {}".format(e))

    finally:
        if client.is_connected:
            await client.disconnect()
            print("Disconnected from the device")
