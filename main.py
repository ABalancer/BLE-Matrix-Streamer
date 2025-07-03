import time
import struct
import asyncio
import threading
import numpy as np
import tkinter as tk
from tkinter import ttk
from tkinter import font
from bleak import BleakClient
from bleak import BleakScanner

from matrix import Matrix


# noinspection SpellCheckingInspection
BASE_UUID = "4A98XXXX-E7C1-EFDE-C757-F1267DD021E8"
MATRIX_SERVICE_UUID = BASE_UUID.replace("XXXX", "1623").lower()
MATRIX_DIMENSIONS_CHARACTERISTIC_UUID = BASE_UUID.replace("XXXX", "1624").lower()
MATRIX_DATA_CHARACTERISTIC_UUID = BASE_UUID.replace("XXXX", "1625").lower()
SCAN_TIME = 10
GRID_SIZE = 500


def remap_matrix(matrix, threshold):
    # Convert the matrix to a NumPy array
    np_matrix = np.array(matrix)
    np_matrix -= threshold
    remapped_matrix = 2*np.where(np_matrix < 0, 0, np_matrix)
    return np.fliplr(remapped_matrix)


def create_widget(parent, widget_type, *args, **kwargs):
    widget = widget_type(parent, *args, **kwargs)

    widget.config(background="#2b2b2b", borderwidth=0, relief=tk.FLAT)
    # Apply the styling based on the current mode (light/dark)
    if widget_type is tk.Canvas or widget_type is Matrix:
        widget.config(highlightthickness=0)
    if widget_type is tk.Label or widget_type is tk.Listbox or widget_type is tk.Button:
        available_fonts = font.families()
        if "JetBrains Mono" in available_fonts:  # Should print True if installed
            font_name="JetBrains Mono"
        else:
            font_name = "Consolas"
        widget.config(foreground="#a8b5c4", font=(font_name, 12))
    if widget_type is tk.Button:
        widget.config(highlightbackground="#2b2b2b", activebackground="#485254",
                      activeforeground="#a8b5c4", background="#3c3f41", width=15, padx=2, pady=2)
    if widget_type is tk.Listbox:
        widget.config(exportselection=False, background="#3c3f41", height=5, activestyle="none")
    return widget


def scale_tuple(input_tuple, x_scale, y_scale, total_rows, total_columns):
    output_tuple = (round((input_tuple[0]) * x_scale / total_columns),
                    round((input_tuple[1]) * y_scale / total_rows))
    return output_tuple


def decode_matrix_dimensions(byte_array):
    num_of_rows, num_of_cols = struct.unpack('<BB', byte_array)
    return num_of_cols, num_of_rows


def decode_matrix_data(byte_array, rows, columns):
    # Unpack the byte array into a flat list of integers
    unpacked_matrix_data = struct.unpack("<" + (rows * columns * "B"), byte_array)
    # Reshape the flat list into a 2D matrix
    matrix_data = [unpacked_matrix_data[i:i + rows] for i in range(0, len(unpacked_matrix_data), columns)]

    return matrix_data


class App:
    def __init__(self, name):
        # Variables
        self.stay_connected = False
        self._devices = [[], [], []]

        # Tkinter
        self.root = tk.Tk()
        self.root.config(background="#2b2b2b")
        self.root.title(name)
        self.root.resizable(False, False)
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.root.iconbitmap("icon.ico")
        self.root.protocol("WM_DELETE_WINDOW", self._exit)

        self.matrix_canvas = None
        self.heat_canvas = None
        self.grid = None
        self.grid_canvas_size = GRID_SIZE
        self._start_time = None
        self._number_of_rows = None
        self._number_of_columns = None
        self._data_format = None
        self._update_matrix = False

        # BLE Box
        self.ble_frame = create_widget(self.root, tk.Frame)
        self.ble_frame.grid(row=0, column=0, sticky="we")
        self.ble_frame.columnconfigure((0, 1, 2), weight=1)

        self.ble_label = create_widget(self.ble_frame, tk.Label, text="BLE Devices:")
        self.ble_label.grid(row=0, column=0, columnspan=3, sticky="w")

        self.devices_listbox = create_widget(self.ble_frame, tk.Listbox)
        self.devices_listbox.grid(row=1, column=0, columnspan=3, stick="nsew")

        self.search_button = create_widget(self.ble_frame, tk.Button,
                                           text="Search", command=self.search_button_callback)
        self.search_button.grid(row=2, column=0)

        self.connect_button = create_widget(self.ble_frame, tk.Button,
                                            text="Connect", command=self.connect_button_callback)
        self.connect_button.grid(row=2, column=1)

        self.disconnect_button = create_widget(self.ble_frame, tk.Button, text="Disconnect",
                                               command=self.disconnect_button_callback)
        self.disconnect_button.grid(row=2, column=2)
        self.root.columnconfigure(0, weight=1)

        # Initialise certain states
        self.connect_disconnect_buttons_state(False)

    def run(self):
        self.root.mainloop()

    def _exit(self):
        if self.stay_connected:
            self.disconnect_button_callback()
        self.root.destroy()

    def create_heatmap_scale(self, width, height, colour_map):
        for x in range(width):
            increment = 4095 * x / width
            colour = colour_map[round(increment)]
            self.heat_canvas.create_line(x, 0, x, height, fill=colour, width=1)


    # Function to trigger searching for devices via a thread
    def search_button_callback(self):
        self.search_button.config(state=tk.DISABLED)
        self.connect_button.config(state=tk.DISABLED)
        self._devices[0].clear()
        self._devices[1].clear()
        self._devices[2].clear()
        self.devices_listbox.delete(0, tk.END)
        threading.Thread(target=lambda: asyncio.run(self._ble_scan_devices()), daemon=True).start()

    # Async function used within thread to start bleak scanner
    async def _ble_scan_devices(self):
        async with BleakScanner(detection_callback=self._device_detection_callback, return_adv=True):
            await asyncio.sleep(SCAN_TIME)
            # noinspection PyTypeChecker
            self.root.after(0, lambda: self.search_button.config(state=tk.NORMAL))
            # noinspection PyTypeChecker
            self.root.after(0, lambda: self.connect_button.config(state=tk.NORMAL))

    def _device_detection_callback(self, device, advertising_data):
        # Updating items after scan response
        if device.address in self._devices[0]:
            index = self._devices[0].index(device.address)
            if self._devices[1][index] is None:
                # A repeated address may be a scan response, which can include extra data such as the device name
                # Remove the previous item in the selection box, and place in a new selection with the device name
                self._devices[1][index] = advertising_data.local_name
                device_string = " {:<25} : {}".format(str(device.name), device.address)
                self.root.after(0, self.devices_listbox.delete, index)
                self.root.after(0, self.devices_listbox.insert, index, device_string)
                # In the case where the service UUID is in the scan response rather than the initial advertising data
                if not self._devices[2][index] and MATRIX_SERVICE_UUID in advertising_data.service_uuids:
                    self._devices[2][index] = True
        # add details of detected device to self.devices list of lists
        elif device.address not in self._devices[0]:
            if MATRIX_SERVICE_UUID in advertising_data.service_uuids:
                allow_connection = True
            else:
                allow_connection = False
            self._devices[0].append(device.address)
            self._devices[1].append(advertising_data.local_name)
            self._devices[2].append(allow_connection)
            device_string = " {:<25} : {}".format(str(device.name), device.address)
            self.root.after(0, self.devices_listbox.insert, tk.END, device_string)

    # Function to connect to device
    def connect_button_callback(self):
        if self.devices_listbox.size() > 0:
            if self._devices[2][self.devices_listbox.curselection()[0]]:
                self.connect_disconnect_buttons_state(True)
                selected_address = self._devices[0][self.devices_listbox.curselection()[0]]
                threading.Thread(target=lambda: asyncio.run(self._ble_connect_stream(selected_address)),
                                 daemon=True).start()
            else:
                print("Selected device does not contain the Matrix Service")

    async def _ble_connect_stream(self, device_address):
        try:
            async with (BleakClient(device_address) as client):
                self.stay_connected = client.is_connected

                matrix_dimensions = await client.read_gatt_char(MATRIX_DIMENSIONS_CHARACTERISTIC_UUID)
                self._number_of_rows, self._number_of_columns = decode_matrix_dimensions(matrix_dimensions)
                self.root.after(0, self.create_matrix, self._number_of_rows, self._number_of_columns)
                self._start_time = time.time()
                await client.start_notify(MATRIX_DATA_CHARACTERISTIC_UUID, self._notification_handler_callback)

                while self.stay_connected:
                    await asyncio.sleep(0.01)
                    if time.time() - self._start_time >= 0.1:
                        self._update_matrix = True

                if client.is_connected:
                    await client.stop_notify(MATRIX_DATA_CHARACTERISTIC_UUID)
                    await client.disconnect()
                    self.root.after(0, self.connect_disconnect_buttons_state, self.stay_connected)
                    # noinspection PyTypeChecker
                    self.root.after(0, self.destroy_matrix)
        except Exception as e:
            print("Connection Failed. Error: {}".format(e))
            self.root.after(0, self.connect_disconnect_buttons_state, False)
            # noinspection PyTypeChecker
            self.root.after(0, self.destroy_matrix)

    # noinspection PyUnusedLocal
    def _notification_handler_callback(self, sender, data):
        matrix_data = decode_matrix_data(data, self._number_of_rows, self._number_of_columns)
        if self._update_matrix:
            self._update_matrix = False
            self._start_time = time.time()
            # matrix_data = remap_matrix(matrix_data, 2048)
            matrix_colours = self.matrix_canvas.match_colours(matrix_data)
            self.root.after(0, self.matrix_canvas.update_matrix, matrix_colours)
            # self.grid.plot_centre_of_pressure(matrix_data)

    # Function to disconnect from the connected device
    def disconnect_button_callback(self):
        self.stay_connected = False

    # toggles the Connect, Disconnect and Search buttons
    def connect_disconnect_buttons_state(self, state):  # if true turn connect button off, disconnect on
        self.connect_button.config(state=tk.DISABLED if state else tk.NORMAL)
        self.search_button.config(state=tk.DISABLED if state else tk.NORMAL)
        self.disconnect_button.config(state=tk.NORMAL if state else tk.DISABLED)

    def create_matrix(self, rows, columns):
        # Canvas matrix grid
        self.matrix_canvas = create_widget(self.root, Matrix, rows=rows, columns=columns, size=self.grid_canvas_size,
                                           borderwidth=0)
        self.matrix_canvas.draw()

        self.heat_canvas = create_widget(self.root, tk.Canvas,
                                         width=self.grid_canvas_size, height=25)
        self.create_heatmap_scale(self.grid_canvas_size, 25, self.matrix_canvas.get_colour_map())
        self.heat_canvas.grid(row=1, column=0)
        self.matrix_canvas.grid(row=2, column=0)

    def destroy_matrix(self):
        if self.matrix_canvas.winfo_exists():
            self.matrix_canvas.destroy()
        if self.heat_canvas.winfo_exists():
            self.heat_canvas.destroy()


if __name__ == "__main__":
    program = App("BLE Matrix Streamer")
    program.run()
