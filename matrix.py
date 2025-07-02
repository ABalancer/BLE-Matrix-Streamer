import tkinter as tk
import numpy as np


colour_interpolation_values = [
    (13, 22, 135), (45, 25, 148), (66, 29, 158), (90, 32, 165), (112, 34, 168),
    (130, 35, 167), (148, 35, 161), (167, 36, 151), (182, 48, 139), (196, 63, 127),
    (208, 77, 115), (220, 93, 102), (231, 109, 92), (239, 126, 79), (247, 143, 68),
    (250, 160, 58), (254, 181, 44), (253, 202, 40), (247, 226, 37), (240, 249, 32)
]


def interpolate_colours(value):
    if value < 4095:
        colour_steps = len(colour_interpolation_values) - 1
        step = 4095 / colour_steps
        start_step = int(value // step)
        end_step = min(start_step + 1, colour_steps)

        start_color = colour_interpolation_values[start_step]
        end_color = colour_interpolation_values[end_step]

        start_r, start_g, start_b = start_color
        end_r, end_g, end_b = end_color

        start_value = start_step * step
        end_value = end_step * step

        ratio = (value - start_value) / (end_value - start_value)
        red = int(start_r + (end_r - start_r) * ratio)
        green = int(start_g + (end_g - start_g) * ratio)
        blue = int(start_b + (end_b - start_b) * ratio)
    else:
        red, green, blue = colour_interpolation_values[-1]
    return f'#{red:02x}{green:02x}{blue:02x}'  # Convert RGB values to hexadecimal color code


def create_colourmap():
    colour_array = []
    for i in range(0, 4096):
        colour_array.append(interpolate_colours(i))
    return colour_array


class Matrix(tk.Canvas):
    def __init__(self, parent, rows, columns, size, **kwargs):
        if rows > columns:
            box_size = (size - 1) / rows
        else:
            box_size = (size - 1) / columns
        super().__init__(parent, width=round(box_size * columns) + 1, height=round(box_size * rows) + 1, **kwargs)
        self._rows = rows
        self._columns = columns
        self._canvas_width = int(self.cget("width"))
        self._canvas_height = int(self.cget("height"))
        self._pc_x_pos = self._canvas_width / 2
        self._pc_y_pos = self._canvas_height / 2
        self._cell_width = box_size
        self._cell_height = box_size
        self._rectangles = []
        self._colour_map = create_colourmap()
        self._base_of_support_lines = None
        self._target_circle = None
        self._pressure_circle = None

    def draw(self):
        for row in range(self._rows):
            for col in range(self._columns):
                x1 = col * self._cell_width
                y1 = row * self._cell_height
                x2 = x1 + self._cell_width
                y2 = y1 + self._cell_height
                rectangle = self.create_rectangle(x1, y1, x2, y2, outline="#777777")
                self._rectangles.append(rectangle)

        self._pressure_circle = self.create_oval(self._canvas_width / 2 - 5, self._canvas_height / 2 - 5,
                                                 self._canvas_width / 2 + 5, self._canvas_height / 2 + 5,
                                                 fill="white", outline="", state="hidden", tag="pressure_circle")

    def edit_rectangle(self, row, col, color):
        index = row * self._columns + col
        if 0 <= index < len(self._rectangles):
            self.itemconfig(self._rectangles[index], fill=color)

    def match_colours(self, matrix_data):
        # Map each value in the matrix to a color
        if self._check_matrix_size(matrix_data):
            try:
                colour_matrix = [[self._colour_map[value] for value in row] for row in matrix_data]
                return colour_matrix
            except IndexError:
                return None
        else:
            return None

    def update_matrix(self, colour_matrix):
        if colour_matrix:
            for row in range(0, self._rows):
                for column in range(0, self._columns):
                    self.edit_rectangle(row, column, colour_matrix[row][column])

    def _check_matrix_size(self, matrix):
        if len(matrix) == self._rows:
            if len(matrix[15]) == self._columns:
                return True
        print("Matrix data did not match with the expected size")
        return False

    def plot_centre_of_pressure(self, matrix_data):
        # Create coordinate matrices for X and Y
        x, y = np.meshgrid(np.arange(matrix_data.shape[1]), np.arange(matrix_data.shape[0]))
        # Calculate total pressure and centroid coordinates
        total_pressure = np.sum(matrix_data)
        if total_pressure > 0:
            centre_x = np.sum(x * matrix_data) / total_pressure
            centre_y = np.sum(y * matrix_data) / total_pressure
            # print("X: {}, Y: {}".format(centre_x, centre_y))
            new_centre_x = self._canvas_width * centre_x / (self._rows - 1)
            new_centre_y = self._canvas_height * centre_y / (self._columns - 1)
            centre_dx = new_centre_x - self._pc_x_pos
            centre_dy = new_centre_y - self._pc_y_pos
            self._pc_x_pos = new_centre_x
            self._pc_y_pos = new_centre_y
            self.move('pressure_circle', centre_dx, centre_dy)
            self.itemconfigure('pressure_circle', state='normal')
        else:
            self.itemconfigure('pressure_circle', state='hidden')

    def get_canvas_dimensions(self):
        return self._canvas_width, self._canvas_height

    def get_colour_map(self):
        return self._colour_map
