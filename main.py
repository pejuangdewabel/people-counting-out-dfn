import cv2
import torch
from sort import Sort
import numpy as np
import warnings
import tkinter as tk
from tkinter import Label, StringVar, Button, PhotoImage, LabelFrame, Entry, messagebox, ttk
from PIL import Image, ImageTk
from dotenv import load_dotenv
import os
import threading
import time
import datetime
import mysql.connector
import uuid
from mysql.connector import Error
from pymongo import MongoClient
from bson.objectid import ObjectId

def generate_unique_filename(base_name, extension='jpg'):
    unique_id = uuid.uuid4().hex
    return f"{base_name}_{unique_id}.{extension}"


def get_config_value_from_mongodb(cctv_code, key):
    # Koneksi ke MongoDB
    client = MongoClient("mongodb://localhost:27017/")
    
    # Memilih database dan koleksi
    db = client["p-c-89012"]
    collection = db["p-crtl-001"]
    
    try:
        # Mengambil dokumen berdasarkan CCTV_CODE
        config = collection.find_one({"CCTV_CODE": cctv_code})
        
        if config:
            # Mengambil nilai berdasarkan key dari dokumen
            return config.get(key, "")
        else:
            raise ValueError("Document not found")
    
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

# Load environment variables from .env file
load_dotenv()

v_id = "DFNOUT01"
# v_id = "DFNIN13"

video_path = get_config_value_from_mongodb(v_id,"RTSP_URL")
capture_folder = get_config_value_from_mongodb(v_id,"CAPTURE_FOLDER")
default_image_path = get_config_value_from_mongodb(v_id,"CAPTURE_IMAGE_DEFAULT") # Path to the default image
detection_active = False
detected_ids = set()
captured_images = []
image_index = 1
person_count = 0

# Create capture folder if it doesn't exist
if not os.path.exists(capture_folder):
    os.makedirs(capture_folder)

# Load YOLOv5 model
warnings.filterwarnings("ignore", category=FutureWarning)
model = torch.hub.load('ultralytics/yolov5', 'yolov5x', pretrained=True)
model.conf = 0.4  # confidence threshold

# Load corrected mask image
mask_img = get_config_value_from_mongodb(v_id,"MASK_IMAGE")
mask_image = cv2.imread(mask_img, cv2.IMREAD_GRAYSCALE)

# Convert mask to color for visualization
color_mask = cv2.cvtColor(mask_image, cv2.COLOR_GRAY2BGR)

# Define colors for different mask areas
color_mask[np.where((mask_image == 255))] = [255, 255, 255]  # White area
color_mask[np.where((mask_image == 0))] = [0, 0, 0]          # Black area
color_mask[np.where((mask_image == 128))] = [0, 0, 255]      # Red area

# Initialize SORT tracker
tracker = Sort()

# Function to check if a point is inside the white mask area
def is_in_white_area(x, y, mask):
    if x < 0 or y < 0 or x >= mask.shape[1] or y >= mask.shape[0]:
        return False
    return mask[y, x] == 255

# Function to check if a point is inside the black mask area
def is_in_black_area(x, y, mask):
    if x < 0 or y < 0 or x >= mask.shape[1] or y >= mask.shape[0]:
        return False
    return mask[y, x] == 0

def validate_group_name(*args):
    """Validate the group name input and enable/disable the Start Detection button."""
    group_name = group_name_var.get() 
    if len(group_name) > 3:
        start_button.config(state=tk.NORMAL)
    else:
        start_button.config(state=tk.DISABLED)

def validate_barcode(*args):
    """Validate the barcode input and enable/disable the Start Detection button."""
    barcode = barcode_var.get().strip()
    
    # Check if barcode length is sufficient
    if len(barcode) >= 20:            
        start_button.config(state=tk.NORMAL)
    else:
        start_button.config(state=tk.DISABLED)


# Fungsi untuk mengubah teks menjadi uppercase
def uppercase_barcode(*args):
    barcode_var.set(barcode_var.get().upper())
    
def on_barcode_enter(event):
    barcode = barcode_var.get().strip()
    if barcode:
        try:
            # Initialize database connection
            connection = mysql.connector.connect(
                host=os.getenv('DB_HOST_RSV'),
                user=os.getenv('DB_USERNAME_RSV'),
                password=os.getenv('DB_PASSWORD_RSV'),
                database=os.getenv('DB_DATABASE_RSV'),
                port=int(os.getenv('DB_PORT_RSV', 3306))
            )
            cursor = connection.cursor()
            
            # Query to find the barcode in the database
            select_query = "SELECT rsvTicketName, rsvOrderNo FROM _reservation WHERE rsvCode = %s"
            cursor.execute(select_query, (barcode,))
            result = cursor.fetchall()

            if result:
                # Update barcode_var with a placeholder value to notify validate_barcode
                show_query_result(barcode)
            else:
                barcode_var.set("")  # Clear barcode_var if no data is found
                messagebox.showwarning("Result", "No data found for the barcode.")
            
            cursor.close()
            connection.close()
        except mysql.connector.Error as e:
            messagebox.showerror("Error", f"Database error: {e}")

def show_query_result(barcode):
    try:
        # Initialize the main database connection
        connection = mysql.connector.connect(
            host=os.getenv('DB_HOST_RSV'),
            user=os.getenv('DB_USERNAME_RSV'),
            password=os.getenv('DB_PASSWORD_RSV'),
            database=os.getenv('DB_DATABASE_RSV'),
            port=int(os.getenv('DB_PORT_RSV', 3306))
        )
        cursor = connection.cursor()

        # Ensure barcode is a string
        if isinstance(barcode, tuple):
            barcode = barcode[0]  # Extract the first element if barcode is a tuple

        # First query to check the rsvCode
        check_query = "SELECT rsvOrderNo, rsvCode FROM _reservation WHERE rsvCode = %s"
        cursor.execute(check_query, (barcode,))
        result = cursor.fetchone()

        if result:
            rsv_order_no = result[0]
            
            # Second query to fetch reservation details
            detail_query = """
            SELECT rsvOrderNo, rsvName, SUM(rsvQty) AS kuota 
            FROM _reservation 
            WHERE rsvOrderNo = %s AND DATE(rsvDate) = '2024-08-04'
            GROUP BY rsvOrderNo
            """
            cursor.execute(detail_query, (rsv_order_no,))
            detail_result = cursor.fetchall()

            if detail_result:
                # Populate form inputs with the result data
                order_id_var.set(detail_result[0][0])
                kuota_var.set(detail_result[0][2])
                group_name_var.set(detail_result[0][1])

                # Initialize a separate connection for inserting into history_scan
                history_connection = mysql.connector.connect(
                    host=os.getenv('DB_HOST'),
                    user=os.getenv('DB_USERNAME'),
                    password=os.getenv('DB_PASSWORD'),
                    database=os.getenv('DB_DATABASE'),
                    port=int(os.getenv('DB_PORT', 3306))
                )
                history_cursor = history_connection.cursor()

                # Insert into history_scan table
                id_device = os.getenv('ID_DEVICE')  # Ensure you have this environment variable set
                current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                insert_query = """
                INSERT INTO history_scan (id_device, barcode, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                """
                history_cursor.execute(insert_query, (id_device, barcode, current_time, current_time))
                history_connection.commit()  # Commit the transaction

                # Close the history database connection
                history_cursor.close()
                history_connection.close()

            else:
                barcode_var.set("")  # Clear barcode_var if no details are found
                messagebox.showinfo("No Data", "No details found for the given order number.")
        else:
            barcode_var.set("")  # Clear barcode_var if no reservation is found
            messagebox.showinfo("No Data", "No reservation found for the given barcode.")

    except mysql.connector.Error as e:
        messagebox.showerror("Error", f"Failed to execute query: {e}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            
def clear_form_inputs():
    barcode_var.set("")
    order_id_var.set("")
    kuota_var.set("")
    group_name_var.set("")
    
    start_button.config(state=tk.DISABLED)



# Initialize Tkinter window
window = tk.Tk()
window.title("People Counting Dashboard")
window.geometry("1280x720")  # Set initial size

# Maximize the window
window.update_idletasks()  # Update window to get current size
window.state('zoomed')     # Maximize the window


window.rowconfigure(0, weight=1)  # Make the row growable
window.columnconfigure([0, 1], weight=1)  # Make both columns growable

logo_path = "icons/logo-acl-a.png"  # Replace with the actual path to your logo
logo_image = PhotoImage(file=logo_path)
window.iconphoto(False, logo_image)

# Create a frame for the title
title_frame = tk.Frame(window)
title_frame.grid(row=0, column=0, columnspan=2, pady=10, sticky="ew")
title_frame.columnconfigure(0, weight=1)

# Create a label for the title centered above the two columns
title_label = Label(title_frame, text="Aplikasi People Counting Dufan Ancol", font=("Helvetica", 20))
title_label.pack(anchor="center")

# Create a label for the title centered above the two columns
title_label = tk.Label(title_frame, text="CCTV Troller", font=("Helvetica", 24))
title_label.pack(anchor="center")

# Create a frame for the left column (video preview)
left_frame = tk.Frame(window)
left_frame.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
left_frame.columnconfigure(0, weight=1)
left_frame.rowconfigure(0, weight=1)

# Create a frame for the right column (count display and controls)
right_frame = tk.Frame(window)
right_frame.grid(row=1, column=1, padx=10, pady=10, sticky="nsew")
right_frame.columnconfigure(0, weight=1)

# Create a label for showing the number of detected people in the right column
people_count_var = StringVar()
people_count_label = Label(right_frame, textvariable=people_count_var, font=("Helvetica", 18, "bold"))
people_count_label.grid(row=0, column=0, pady=5)


# Create a label for the video frame in the left column
video_label = Label(left_frame)
video_label.grid(row=0, column=0, sticky="nsew")


# Example of label creation (assumes a Tkinter window and layout are already set up)
status_label = Label(right_frame, text="Detection: Stopped", fg="white", bg="orange", font=("Arial", 12, "bold"), padx=20, pady=10)
status_label.grid(row=1, column=0, pady=20) 

# Add a LabelFrame for the group name input below the status label
group_label_frame = LabelFrame(right_frame, text="Please Enter Detail", font=("Helvetica", 12), padx=5, pady=5)
group_label_frame.grid(row=2, column=0, pady=10, sticky="ew")

# Input untuk Barcode
barcode_var = StringVar()
barcode_var.trace_add("write", uppercase_barcode)
barcode_var.trace_add("write", validate_barcode) 

barcode_label = tk.Label(group_label_frame, text="Enter Barcode:", font=("Helvetica", 12))
barcode_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")

barcode_entry = tk.Entry(group_label_frame, textvariable=barcode_var, font=("Helvetica", 12))
barcode_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
barcode_entry.bind("<Return>", on_barcode_enter)

# Add a label and entry for the OrderID inside the LabelFrame
order_id_var = StringVar()

order_id_label = tk.Label(group_label_frame, text="Order ID:", font=("Helvetica", 12))
order_id_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")

order_id_entry = tk.Entry(group_label_frame, textvariable=order_id_var, font=("Helvetica", 12))
order_id_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")

order_id_entry.config(state='readonly')

# Add a label and entry for the Kuota inside the LabelFrame
kuota_var = StringVar()

kuota_label = tk.Label(group_label_frame, text="Kuota:", font=("Helvetica", 12))
kuota_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")

kuota_entry = tk.Entry(group_label_frame, textvariable=kuota_var, font=("Helvetica", 12))
kuota_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")

kuota_entry.config(state='readonly')

# Add a label and entry for the group name inside the LabelFrame
group_name_var = StringVar()
group_name_var.trace_add("write", validate_group_name)
group_name_label = tk.Label(group_label_frame, text="Group Name:", font=("Helvetica", 12))
group_name_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")

group_name_entry = tk.Entry(group_label_frame, textvariable=group_name_var, font=("Helvetica", 12))
group_name_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

group_name_entry.config(state='readonly')

# Optionally, adjust the row configuration of the grid to accommodate new rows
group_label_frame.grid_rowconfigure(4, weight=1)

# Make sure to adjust column weights to allow entries to expand
group_label_frame.grid_columnconfigure(1, weight=1)

# Sampai sini

# Load and resize icons
def load_and_resize_icon(path, size):
    icon = PhotoImage(file=path)
    icon = icon.subsample(icon.width() // size[0], icon.height() // size[1])
    return icon

# Desired size for the buttons
button_size = (150, 50)  # Adjusted size

start_icon = load_and_resize_icon('icons/start.png', (20, 20))  # Smaller icon size
stop_icon = load_and_resize_icon('icons/stop.png', (20, 20))  # Smaller icon size
clear_icon = load_and_resize_icon('icons/clear.png', (20, 20))  # Smaller icon size

# Create buttons for starting and stopping detection
start_button = Button(
    right_frame, 
    text="(F5) Start", 
    command=lambda: set_detection_state(True),
    image=start_icon,
    compound=tk.LEFT,  # Text and image side by side
    bg="#4CAF50",     # Custom background color (green)
    fg="#ffffff",       # Custom text color
    font=("Helvetica", 14),
    width=button_size[0],  # Set button width
    height=button_size[1],  # Set button height
    padx=10,          # Add horizontal padding
    pady=5,            # Add vertical padding
    state=tk.DISABLED
)
start_button.grid(row=3, column=0, pady=5)

stop_button = Button(
    right_frame, 
    text=" (F6) Stop", 
    command=lambda: confirm_stop_detection(),
    image=stop_icon,
    compound=tk.LEFT,  # Text and image side by side
    bg="#ed5353",     # Custom background color (red)
    fg="#ffffff",       # Custom text color
    font=("Helvetica", 14),
    width=button_size[0],  # Set button width
    height=button_size[1],  # Set button height
    padx=10,          # Add horizontal padding
    pady=5,        # Add vertical padding
    state=tk.DISABLED
)
stop_button.grid(row=4, column=0, pady=5)

clear_button = Button(
    right_frame,
    text=" (F7) Clear",
    command=clear_form_inputs,
    image=clear_icon,  # Assuming you have a `clear_icon`, similar to `start_icon`
    compound=tk.LEFT,  # Text and image side by side
    bg="#E85C0D",  # Custom background color (green)
    fg="white",    # Custom text color (white)
    font=("Helvetica", 14),
    width=button_size[0],  # Set button width
    height=button_size[1],  # Set button height
    padx=10,  # Add horizontal padding
    pady=5    # Add vertical padding
)
clear_button.grid(row=5, column=0, pady=5)


# Create a frame for the capture images list and move it to the left column (below the video preview)
capture_frame = LabelFrame(left_frame, text="Capture People", padx=10, pady=10, borderwidth=2, relief="solid")
capture_frame.grid(row=1, column=0, pady=10, sticky="nsew")

# Create labels for displaying captured images with borders (picture boxes)
image_labels = [Label(capture_frame, relief="solid", borderwidth=2, width=100, height=100) for _ in range(10)]
for label in image_labels:
    label.pack(side=tk.LEFT, padx=5)


# Load and set the default image
def set_default_image():
    try:
        default_image = Image.open(default_image_path)
        default_image = default_image.resize((100, 100), Image.Resampling.LANCZOS)  # Use Resampling.LANCZOS
        default_photo = ImageTk.PhotoImage(default_image)
        for label in image_labels:
            label.config(image=default_photo)
            label.image = default_photo
    except Exception as e:
        messagebox.showerror("Error", f"Failed to load default image: {e}")

set_default_image()

# Create a frame for the footer
footer_frame = tk.Frame(window, bg="#41BDD9")
footer_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=10)

# Add copyright label to the footer frame
footer_label = tk.Label(footer_frame, text=f"© 2024 IT & SPM Division. All rights reserved. {os.getenv('ID_DEVICE')}", bg="#41BDD9")
footer_label.pack()

# Flag for controlling detection
detection_active = False

# Set to track detected IDs
detected_ids = set()

# List to hold captured images
captured_images = []

# Index for cycling through captured images
image_index = 0

# Function to start or stop detection
# Define the function to set the detection state
def set_detection_state(active):
    global capture_folder
    global detection_active
    global image_index

    detection_active = active
    group_name = group_name_var.get().strip()

    if active:
        if len(group_name) < 3:
            status_label.config(text="❌ Group Name must be at least 3 characters long", fg="red", bg="yellow")
            return
        
        # Create folder inside 'captures' with orderid-groupname-timestamp format
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        order_id = order_id_var.get().strip()  # Get the order ID from the form input
        group_name = group_name_var.get().strip()  # Get the group name from the form input

        formatted_group_name = group_name.lower().replace(" ", "_")
        new_folder_name = f'{order_id}-{formatted_group_name}-{timestamp}'  # Folder name format: orderid-groupname-timestamp
        new_folder_path = os.path.join(capture_folder, new_folder_name)

        if not os.path.exists(new_folder_path):
            os.makedirs(new_folder_path)

        capture_folder = new_folder_path
        image_index = 1 

        # Update UI
        barcode_entry.config(state=tk.DISABLED)
        group_name_entry.config(state=tk.DISABLED)
        kuota_entry.config(state=tk.DISABLED)
        order_id_entry.config(state=tk.DISABLED)

        start_button.config(state=tk.DISABLED)
        stop_button.config(state=tk.NORMAL)
        clear_button.config(state=tk.DISABLED)
        status_label.config(text="✔️ Detection: Running", fg="white", bg="blue")
        animate_status_label(status_label)  # Optionally add a simple animation
    else:
        stop_detection_and_save()

def confirm_stop_detection():
    confirmation = messagebox.askyesno("Stop Detection", "Are you sure you want to stop the detection?")
    if confirmation:
        stop_detection_and_save()  # Call the stop detection function only if the user confirms

def stop_detection_and_save():
    global detection_active
    global capture_folder
    global detected_ids
    global captured_images
    global image_index
    global person_count
    global group_name_var

    detection_active = False  # Stop detection
    uid_folder = os.path.basename(capture_folder) 
    capture_folder = os.getenv('CAPTURE_FOLDER', 'default_capture_folder')  # Reset capture folder to default

    # Insert data into database
    try:
        # Initialize the connection
        connection = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            user=os.getenv('DB_USERNAME'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_DATABASE'),
            port=int(os.getenv('DB_PORT', 3306))
        )
        cursor = connection.cursor()
        
        total_count = len(detected_ids)  # Use len() to get the count of detected IDs

        if total_count > 0:
            insert_query = """
                INSERT INTO history_counting (id_device, order_id, group_name, name_folder, total_count, kuota_ticket, created_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            data = (
                os.getenv('ID_DEVICE'),
                order_id_var.get().strip(),  # Get order_id from form input
                group_name_var.get().strip(),
                uid_folder,
                total_count,
                kuota_entry.get().strip(),  # Get kuota_ticket from form input
                datetime.datetime.now()
            )
            cursor.execute(insert_query, data)
            connection.commit()
            
            # Show success message
            messagebox.showinfo("Success", "Data successfully inserted into the database.")
        else:
            # Show warning if total_count is 0
            messagebox.showwarning("Warning", "Total count is 0. No data inserted into the database.")
        
        clear_form_inputs()

        # Update UI
        barcode_entry.config(state=tk.NORMAL)
        group_name_entry.config(state=tk.DISABLED)
        start_button.config(state=tk.DISABLED)
        stop_button.config(state=tk.DISABLED)
        clear_button.config(state=tk.NORMAL)
        group_name_var.set("")
        status_label.config(text="❌ Detection: Stopped", fg="white", bg="orange")
        animate_status_label(status_label)  # Optionally add a simple animation
        
        # Reset images and people count
        captured_images.clear()
        image_index = 1
        person_count = 0
        detected_ids.clear() # Clear detected IDs
        people_count_var.set("")
        update_image_labels()  # Reset image labels
        set_default_image()

    except Error as e:
        messagebox.showerror("Error", f"Failed to insert data: {e}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
        
# Function to handle window close event
def on_closing():
    if detection_active:
        if messagebox.askyesno("Detection Active", "Detection is still running. Do you want to stop it and close the application?"):
            set_detection_state(False)  # Stop detection before closing
            window.destroy()
    else:
        if messagebox.askyesno("Exit", "Are you sure you want to exit the application?"):
            window.destroy()

# Function to show About dialog
def show_about():
    messagebox.showinfo("About", "People Counting Dashboard\nVersion 1.0\nDeveloped by IT & SPM Division \nPT Pembangunan Jaya Ancol, Tbk")
    
# Function to test database connection
def test_db_connection():
    try:
        # Load environment variables
        env_vars = {
            'DB_HOST': os.getenv('DB_HOST'),
            'DB_USERNAME': os.getenv('DB_USERNAME'),
            'DB_PASSWORD': os.getenv('DB_PASSWORD'),
            'DB_DATABASE': os.getenv('DB_DATABASE'),
            'DB_PORT': os.getenv('DB_PORT', 3306)
        }
        
        # Connect to the database
        connection = mysql.connector.connect(
            host=env_vars['DB_HOST'],
            user=env_vars['DB_USERNAME'],
            password=env_vars['DB_PASSWORD'],
            database=env_vars['DB_DATABASE'],
            port=int(env_vars['DB_PORT'])
        )
        
        if connection.is_connected():
            messagebox.showinfo("Info", "Database connection successful")
        else:
            messagebox.showwarning("Warning", "Database connection failed")
    
    except Error as e:
        messagebox.showerror("Error", f"Database connection failed: {e}")
    
    finally:
        if connection.is_connected():
            connection.close()

# Function to view history
def view_history():
    try:
        # Initialize the connection
        connection = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            user=os.getenv('DB_USERNAME'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_DATABASE'),
            port=int(os.getenv('DB_PORT', 3306))
        )
        cursor = connection.cursor()
        
        # Fetch data from the history_counting table using parameterized query
        select_query = """
            SELECT id_device, order_id, group_name, name_folder, kuota_ticket, total_count, created_date
            FROM history_counting
            WHERE id_device = %s
            ORDER BY created_date DESC
        """
        cursor.execute(select_query, (os.getenv('ID_DEVICE'),))
        history_data = cursor.fetchall()

        # Create a new window to display history
        history_window = tk.Toplevel()
        history_window.title("View History")
        
        # Set window size and center it on the screen
        window_width = 800  # Adjusted window width to accommodate additional columns
        window_height = 400

        screen_width = history_window.winfo_screenwidth()
        screen_height = history_window.winfo_screenheight()

        x = (screen_width // 2) - (window_width // 2)
        y = (screen_height // 2) - (window_height // 2)

        history_window.geometry(f"{window_width}x{window_height}+{x}+{y}")

        # Create Treeview widget to display history data
        tree = ttk.Treeview(history_window, columns=("ID Device", "Order ID", "Group Name", "Name Folder","Kuota Ticket", "Total Count", "Created Date"), show='headings')
        tree.heading("ID Device", text="ID Device")
        tree.heading("Order ID", text="Order ID")
        tree.heading("Group Name", text="Group Name")
        tree.heading("Name Folder", text="Name Folder")
        tree.heading("Kuota Ticket", text="Kuota Ticket")
        tree.heading("Total Count", text="Total Count")
        tree.heading("Created Date", text="Created Date")

        # Set column widths
        tree.column("ID Device", width=100)
        tree.column("Order ID", width=100)
        tree.column("Group Name", width=150)
        tree.column("Name Folder", width=150)
        tree.column("Kuota Ticket", width=150)
        tree.column("Total Count", width=100)
        tree.column("Created Date", width=150)
        
        # Insert history data into the treeview
        for row in history_data:
            tree.insert("", tk.END, values=row)
        
        tree.pack(fill=tk.BOTH, expand=True)

    except mysql.connector.Error as e:
        messagebox.showerror("Error", f"Failed to fetch data: {e}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()


# Create a menu bar
menu_bar = tk.Menu(window)

# Add a "Tools" menu
tools_menu = tk.Menu(menu_bar, tearoff=0)
tools_menu.add_command(label="View History", command=view_history)
tools_menu.add_command(label="Test Database Connection", command=test_db_connection)
tools_menu.add_command(label="About", command=show_about)
menu_bar.add_cascade(label="Menu", menu=tools_menu)

# Configure the window to display the menu bar
window.config(menu=menu_bar)

# Bind the close event to the on_closing function
window.protocol("WM_DELETE_WINDOW", on_closing)

# Function to toggle detection state using F5
window.bind('<F5>', lambda event: set_detection_state(True))
window.bind('<F6>', lambda event: set_detection_state(False))

def animate_status_label(label):
    # Simple animation: Fade effect by alternating visibility
    for _ in range(3):
        label.after(200, lambda: label.config(fg="black"))
        label.update()
        time.sleep(0.2)
        label.after(200, lambda: label.config(fg="white"))
        label.update()
        time.sleep(0.2)

# Function to update the UI with video frames
def update_frame(frame):
    # Convert the frame to PIL image and then to ImageTk
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    photo = ImageTk.PhotoImage(image=image)
    
    # Update the label with the new image
    video_label.config(image=photo)
    video_label.image = photo

# Function to capture and save the cropped image
def capture_image(frame, bbox, count):
    x1, y1, x2, y2 = bbox
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    
    # Crop the image using bounding box
    cropped_image = frame[y1:y2, x1:x2]
    
    # Generate a unique filename
    unique_filename = f"person_{uuid.uuid4().hex}.jpg"
    file_path = os.path.join(capture_folder, unique_filename)
    
    # Save the cropped image
    cv2.imwrite(file_path, cropped_image)
    
    # Add the captured image to the list
    captured_images.append(file_path)
    if len(captured_images) > 10:
        captured_images.pop(0)  # Keep only the latest 10 images
    
    # Update the image labels
    update_image_labels()


# Function to update image labels
def update_image_labels():
    for i, label in enumerate(image_labels):
        if i < len(captured_images):
            image = Image.open(captured_images[i])
            image.thumbnail((100, 100))  # Resize for display in smaller size
            photo = ImageTk.PhotoImage(image=image)
        else:
            # Create a blank image with a border
            default_image = Image.open(default_image_path)
            default_image = default_image.resize((100, 100), Image.Resampling.LANCZOS)  # Updated to use Resampling.LANCZOS
            photo = ImageTk.PhotoImage(image=default_image)

        label.config(image=photo)
        label.image = photo

# Function to process video frames and detect objects
def detect_and_display():
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
    
    person_count = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            print("Error reading frame, skipping...")
            continue

        # Resize frame to 960x540
        frame = cv2.resize(frame, (960, 540))

        # Overlay the mask onto the frame with some transparency
        alpha = 0.4  # Transparency factor
        overlay_frame = cv2.addWeighted(color_mask, alpha, frame, 1 - alpha, 0)

        # Check if detection is active
        if detection_active:
            # Perform detection
            results = model(frame)

            # Extract bounding boxes and confidences
            dets = []
            for det in results.xyxy[0].cpu().numpy():
                x1, y1, x2, y2, conf, cls = det
                if cls == 0:  # class 0 is 'person'
                    dets.append([x1, y1, x2, y2, conf])

            # Convert detections to numpy array
            if len(dets) > 0:
                dets = np.array(dets)
            else:
                dets = np.empty((0, 5))

            # Update tracker
            trackers = tracker.update(dets)

            # Draw bounding boxes, midpoints, and count people
            count = 0
            for track in trackers:
                x1, y1, x2, y2, track_id = track
                x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                
                # Calculate midpoint
                mid_x = (x1 + x2) // 2
                mid_y = (y1 + y2) // 2

                # Draw midpoint
                cv2.circle(overlay_frame, (mid_x, mid_y), 5, (0, 255, 0), -1)

                # Check if the midpoint is in the black area
                if is_in_black_area(mid_x, mid_y, mask_image):
                    continue  # Ignore detection if in black area

                # Check if the midpoint is in the white area
                if is_in_white_area(mid_x, mid_y, mask_image):
                    count += 1
                    if track_id not in detected_ids:
                        detected_ids.add(track_id)
                        person_count += 1
                        capture_image(frame, (x1, y1, x2, y2), person_count)
                    
                    # Draw bounding box and ID
                    cv2.rectangle(overlay_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(overlay_frame, f'ID: {int(track_id)}', (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            # Update people count label
            people_count_var.set(f"Total People Count: {len(detected_ids)}")
            # people_count_var.set(f"Total People Count: {len(detected_ids)} (Detected: {count})")

        # Display the frame with overlay
        update_frame(overlay_frame)

        # Wait briefly before processing the next frame
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()

# Start the detection process in a separate thread
def start_detection():
    detection_thread = threading.Thread(target=detect_and_display)
    detection_thread.daemon = True
    detection_thread.start()

# Start detection automatically
start_detection()

# Start the Tkinter main loop
window.mainloop()
