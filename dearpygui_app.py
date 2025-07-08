import time
import struct
import asyncio
import threading
from queue import Queue
import dearpygui.dearpygui as dpg
from bleak import BleakScanner, BleakClient

'''
TODO: FPS Counter, Data Rate Counter
Improve Update Matrix thread
Improve Connect and Disconnect stages
'''


# noinspection SpellCheckingInspection
BASE_UUID = "4A98XXXX-E7C1-EFDE-C757-F1267DD021E8"
MATRIX_SERVICE_UUID = BASE_UUID.replace("XXXX", "1623").lower()
MATRIX_DIMENSIONS_CHARACTERISTIC_UUID = BASE_UUID.replace("XXXX", "1624").lower()
MATRIX_DATA_CHARACTERISTIC_UUID = BASE_UUID.replace("XXXX", "1625").lower()
TIMEOUT_SECONDS = 20  # How long to wait until removing a last seen device
GRID_SIZE = 500
MATRIX_FRAME_RATE = 30


COLOUR_MAP_VALUES = [
    ( 13,  22, 135, 255), ( 45,  25, 148, 255), ( 66,  29, 158, 255), ( 90,  32, 165, 255), (112,  34, 168, 255),
    (130,  35, 167, 255), (148,  35, 161, 255), (167,  36, 151, 255), (182,  48, 139, 255), (196,  63, 127, 255),
    (208,  77, 115, 255), (220,  93, 102, 255), (231, 109,  92, 255), (239, 126,  79, 255), (247, 143,  68, 255),
    (250, 160,  58, 255), (254, 181,  44, 255), (253, 202,  40, 255), (247, 226,  37, 255), (240, 249,  32, 255)
]


class BLEFrameAssembler:
    def __init__(self, timeout=1.0):
        self.frames = {}  # frame_id -> list of parts
        self.expected_parts = {}  # frame_id -> total_parts
        self.timestamps = {}  # frame_id -> timestamp
        self.timeout = timeout  # seconds

    def construct_data(self, data: bytes):
        now = time.time()

        # Clean up old frames
        self._cleanup_old_frames(now)

        if len(data) < 3:
            print("Invalid packet: too short")
            return None

        frame_id = data[0]
        total_parts = data[1]
        part_number = data[2]
        payload = data[3:]

        # Ignore invalid part numbers
        if part_number >= total_parts:
            print(f"Invalid part number {part_number} for frame {frame_id}")
            return None

        # Initialize frame if new
        if frame_id not in self.frames:
            self.frames[frame_id] = [None] * total_parts
            self.expected_parts[frame_id] = total_parts
            self.timestamps[frame_id] = now

        # Store the part
        self.frames[frame_id][part_number] = payload

        # Check if all parts are received
        if all(part is not None for part in self.frames[frame_id]):
            full_payload = b''.join(self.frames[frame_id])

            # Cleanup
            del self.frames[frame_id]
            del self.expected_parts[frame_id]
            del self.timestamps[frame_id]

            # print(f"Frame {frame_id} reassembled successfully.")
            return full_payload

        return None  # Not yet complete

    def _cleanup_old_frames(self, current_time):
        expired = [fid for fid, t in self.timestamps.items()
                   if current_time - t > self.timeout]
        for fid in expired:
            print(f"Frame {fid} expired. Cleaning up.")
            self.frames.pop(fid, None)
            self.expected_parts.pop(fid, None)
            self.timestamps.pop(fid, None)


class BLEScanner:
    def __init__(self, add_new_device_callback, update_device_callback, delete_device_callback):
        self._new_device_cb = add_new_device_callback
        self._update_device_cb = update_device_callback
        self._delete_device_cb = delete_device_callback

        self.devices = {}  # address -> (name, matrix_service, last_seen)

        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._stop_event = threading.Event()

        self._discover_devices_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._remove_stale_device_thread = threading.Thread(target=self._remove_stale_devices, daemon=True)

    def __del__(self):
        if not self._stop_event.is_set():
            self.stop()
        print("Successfully Exited BLEScanner Threads")

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._scanner())

    async def _scanner(self):
        async with BleakScanner(detection_callback=self._device_found_cb, return_adv=True):
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)
                continue

    def _device_found_cb(self, device, adv_data):
        name = None
        has_service = None
        time_stamp = time.time()
        # Update with scan response data
        if device.address in self.devices:
            if self.devices[device.address][0] == "Unknown":
                name = adv_data.local_name or "Unknown"
                self._update_device_cb(device.address, "name", name)
            if not self.devices[device.address][1]:
                has_service = str(MATRIX_SERVICE_UUID in adv_data.service_uuids)
                self._update_device_cb(device.address, "service", has_service)
        # If it is a new device:
        else:
            name = adv_data.local_name or "Unknown"
            has_service = str(MATRIX_SERVICE_UUID in adv_data.service_uuids)
            self._new_device_cb(device.address, name, has_service)

        with self._lock:
            self.devices[device.address] = (name, has_service, time_stamp)

    def _remove_stale_devices(self):
        while not self._stop_event.is_set():
            time.sleep(0.5)
            now = time.time()
            with self._lock:
                    addresses_to_pop = []
                    for addr, (name, service, ts) in self.devices.items():
                        if now - ts >= TIMEOUT_SECONDS:
                            self._delete_device_cb(addr)
                            addresses_to_pop.append(addr)
                    for addr in addresses_to_pop:
                        self.devices.pop(addr)

    def start(self):
        self._discover_devices_thread.start()
        self._remove_stale_device_thread.start()

    def stop(self):
        self._stop_event.set()
        self._discover_devices_thread.join()
        self._remove_stale_device_thread.join()

    def get_devices(self):
        with self._lock:
            return dict(self.devices)


class BLEConnection:
    def __init__(self, address, create_matrix_callback, delete_matrix_callback):
        self._create_matrix_cb = create_matrix_callback
        self._delete_matrix_cb = delete_matrix_callback

        self.matrix_data_queue = Queue()
        self._dimensions_characteristic = MATRIX_DIMENSIONS_CHARACTERISTIC_UUID
        self._data_stream_characteristic = MATRIX_DATA_CHARACTERISTIC_UUID
        self._address = address

        self._rows = None
        self._columns = None
        self._data_assembler = BLEFrameAssembler()
        self._update_matrix = False
        self._data_rate_start_time = 0
        self._assembled_data_count = 0
        self._start_time = 0

        self._loop = asyncio.new_event_loop()
        self._stop_event = threading.Event()
        self.mutex = threading.Lock()

        self._connection_thread = threading.Thread(target=self._run_loop, daemon=True)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._ble_connect_stream())

    async def _ble_connect_stream(self):
        try:
            async with (BleakClient(self._address) as self._client):
                self._rows, self._columns = await self._get_matrix_dimensions()
                self._create_matrix_cb(self._rows, self._columns)
                self._start_times = time.time()
                await self._client.start_notify(self._data_stream_characteristic, self._notification_handler_callback)

                while not self._stop_event.is_set():
                    await asyncio.sleep(0.1)

                if self._stop_event.is_set():
                    await self._client.stop_notify(MATRIX_DATA_CHARACTERISTIC_UUID)
                    await self._client.disconnect()
                    self._delete_matrix_cb()

        except Exception as e:
            print("Connection Failed. Error: {}".format(e))
            self._delete_matrix_cb()

    async def _get_matrix_dimensions(self):
        byte_array = await self._client.read_gatt_char(self._dimensions_characteristic)
        rows, columns = struct.unpack('<BB', byte_array)
        return rows, columns

    def _decode_matrix_data(self, byte_array):
        # Unpack the byte array into a flat list of integers
        unpacked_matrix_data = struct.unpack("<" + (self._rows * self._columns * "B"), byte_array)
        return unpacked_matrix_data

    # noinspection PyUnusedLocal
    def _notification_handler_callback(self, sender, data):
        assembled_data = self._data_assembler.construct_data(data)
        if assembled_data is not None:
            matrix_values = self._decode_matrix_data(assembled_data)
            with self.mutex:
                self.matrix_data_queue.put(matrix_values)

            self._assembled_data_count += 1
            data_rate_time_difference = time.time() - self._data_rate_start_time
            if data_rate_time_difference >= 5:
                data_rate = self._assembled_data_count / data_rate_time_difference
                print("Data Rate: {:.2f}/s".format(data_rate))
                self._data_rate_start_time = time.time()
                self._assembled_data_count = 0

    def start(self):
        self._connection_thread.start()

    def stop(self):
        self._stop_event.set()
        self._connection_thread.join()
        with self.mutex:
            while not self.matrix_data_queue.empty():
                self.matrix_data_queue.get_nowait()


class MatrixApp:
    def __init__(self):
        self._scanner = None
        self._device_table_items = {}

        self._connecting_group = None
        self._connector = None
        self._colormap = None
        self._pressure_matrix = None
        self._pressure_matrix_group = None
        self._pressure_matrix_update_handler = None
        self._last_gui_update = time.time()

    def setup_app(self):
        # GUI setup
        dpg.create_context()
        dpg.create_viewport(title='BLE Matrix Streamer', vsync=True, resizable=False, small_icon="./icon.ico", large_icon="./icon_png.png")

        with dpg.font_registry():
            regular_font = dpg.add_font(file="./JetBrainsMono-Regular.ttf", size=20)

        with (dpg.colormap_registry()):
            self._colormap = dpg.add_colormap(colors=COLOUR_MAP_VALUES, qualitative=False)

        with dpg.window(tag="Primary Window") as self.window:
            dpg.bind_font(regular_font)
            self._create_device_scanning_table()

        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 12, category=dpg.mvThemeCat_Core)

        with dpg.item_handler_registry() as self._pressure_matrix_update_handler:
             dpg.add_item_visible_handler(callback=self.update_pressure_matrix_callback)

        dpg.set_primary_window("Primary Window", True)
        dpg.bind_theme(global_theme)

        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.start_dearpygui()
        self._on_close()
        dpg.destroy_context()

    def _create_device_scanning_table(self):
        dpg.configure_viewport(0, width=400, height=400)
        with (dpg.group(parent=self.window) as self.scan_results_group):
            dpg.add_text("Scanning for Bluetooth Devices")
            with dpg.table(header_row=True, scrollY=True, resizable=False, reorderable=False, hideable=False,
                           borders_innerV=True, borders_innerH=True, borders_outerH=True, borders_outerV=True) as self.device_table_rows:
                dpg.add_table_column(label="Address", width_fixed=True)
                dpg.add_table_column(label="Device")

        self._scanner = BLEScanner(self.add_row_to_table, self.update_row_in_table, self.delete_row_in_table)
        self._scanner.start()

    def _remove_device_scanning_table(self):
        self._scanner.stop()
        self._scanner = None

        self._device_table_items.clear()
        dpg.delete_item(self.scan_results_group)

    def add_row_to_table(self, address, name, has_service):
        device_details = [address, name]
        items = []
        with dpg.table_row(parent=self.device_table_rows) as row:
            items.append(row)
            for j in range(0, len(device_details)):
                if has_service == "True":
                    items.append(dpg.add_selectable(label=device_details[j], span_columns=True, callback=self.connect_to_device, user_data=address))
                else:
                    items.append(dpg.add_selectable(label=device_details[j], span_columns=True, enabled=False))
                self._device_table_items[address] = items

    def update_row_in_table(self, address, update_parameter, update_data):
        if address in self._device_table_items:
            if update_parameter == "name":
                dpg.set_item_label(self._device_table_items[address][2], update_data)
            elif update_parameter == "service":
                if update_data == "True":
                    for item in self._device_table_items[address][1:]:
                        dpg.enable_item(item)
                else:
                    for item in self._device_table_items[address][1:]:
                        dpg.disable_item(item)

    def delete_row_in_table(self, address):
        if address in self._device_table_items:
            dpg.delete_item(self._device_table_items[address][0])
            self._device_table_items.pop(address)

    def create_pressure_matrix(self, rows, columns):
        width = GRID_SIZE if columns > rows else round(GRID_SIZE * columns / rows)
        height = round(GRID_SIZE * rows / columns) if columns > rows else GRID_SIZE
        dpg.configure_viewport(0, width=115 + width, height=85 + GRID_SIZE)
        values = [255] * rows * columns

        with dpg.group(parent=self.window) as self._pressure_matrix_group:
            dpg.add_button(label="Disconnect", width=120, callback=self.disconnect_from_device)
            with dpg.group(horizontal=True):
                color_map_scale = dpg.add_colormap_scale(min_scale=0, max_scale=255, height=GRID_SIZE, colormap=self._colormap)
                with dpg.plot(before=color_map_scale, no_title=True, no_mouse_pos=True, no_inputs=True, height=height, width=width) as plot:
                    dpg.bind_colormap(plot, self._colormap)
                    dpg.add_plot_axis(dpg.mvXAxis, lock_min=True, lock_max=True, no_gridlines=True, no_tick_marks=True, no_label=True, no_tick_labels=True)
                    with dpg.plot_axis(dpg.mvYAxis, no_gridlines=True, no_tick_marks=True, lock_min=True, lock_max=True, no_label=True, no_tick_labels=True):
                        self._pressure_matrix = dpg.add_heat_series(values, rows, columns, scale_min=0, scale_max=255, format="%.f")

        dpg.bind_item_handler_registry(self._pressure_matrix_group, self._pressure_matrix_update_handler)

    # noinspection PyUnusedLocal
    def update_pressure_matrix_callback(self, sender, app_data, user_data):
        if self._pressure_matrix_group is not None:
            now = time.time()
            time_difference = now - self._last_gui_update
            if time_difference >= 1 / MATRIX_FRAME_RATE:
                self._last_gui_update = now
                latest_matrix_data = None
                with self._connector.mutex:
                    # Track queue size:
                    # print(self._connector.matrix_data_queue.qsize() / time_difference)
                    while not self._connector.matrix_data_queue.empty():
                        latest_matrix_data = self._connector.matrix_data_queue.get_nowait()
                        # process_matrix(latest_data)  could do data rate stuff here
                if latest_matrix_data is not None:
                    dpg.set_value(self._pressure_matrix, [latest_matrix_data])


    def remove_pressure_matrix(self):
        if self._pressure_matrix_group:
            dpg.delete_item(self._pressure_matrix_group)
            self._pressure_matrix_group = None
        self._create_device_scanning_table()

    # noinspection PyUnusedLocal
    def connect_to_device(self, sender, app_data, address):
        for _, [_, address_item, name_item] in self._device_table_items.items():
            dpg.disable_item(address_item)
            dpg.disable_item(name_item)
        self._remove_device_scanning_table()
        self._connector = BLEConnection(address, self.create_pressure_matrix, self.remove_pressure_matrix)
        self._connector.start()

    def connecting_animation(self, address):
        with dpg.add_group(parent=self.window) as self._connecting_group:
            dpg.add_text("Attempting to connect to device:")
            dpg.add_text(address)

    # noinspection PyUnusedLocal
    def disconnect_from_device(self, sender, app_data):
        self._connector.stop()
        self._connector = None

    #def disconnect_animation(self):


    def _on_close(self):
        if self._scanner is not None:
            self._scanner.stop()
        if self._connector is not None:
            self._connector.stop()
        print("GUI Closed Safely")


if __name__ == "__main__":
    app = MatrixApp()
    app.setup_app()

