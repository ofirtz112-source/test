from datetime import datetime
from database import Database

db = Database()

#Converts a datetime value to a readable string format for display in HTML
def _format_datetime(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return str(value)
    return dt.strftime("%d %b %Y, %H:%M")

#Formats a price value as a dollar amount for display
def _format_price(value):
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
        return f"${n:,.2f}" if not n.is_integer() else f"${int(n):,}"
    except (ValueError, TypeError):
        return f"${value}"

#Prepares flight data for display by formatting dates and prices
def prepare_flights_for_view(flights):
    prepared = []
    for f in flights or []:
        f = dict(f)
        f["departure_display"] = _format_datetime(f.get("departure_time"))
        f["arrival_display"] = _format_datetime(f.get("arrival_time"))
        f["price_display"] = _format_price(f.get("min_price"))
        prepared.append(f)
    return prepared

#Creates and returns a plane object with the correct dimensions based on the flight data
def get_plane_object(flight_id):
    """Factory: יוצר אובייקט מטוס עם המימדים הנכונים"""
    from models import SmallPlane, BigPlane
    plane_details = db.get_plane_details_for_seatmap(flight_id)
    if not plane_details:
        return None

    class_dims = db.get_class_dimensions(plane_details["id_plane"])
    if not class_dims:
        return None

    #Finds and returns the class dimension data for a given class name, ignoring case and extra spaces.
    def find_dim(c_name):
        for c in class_dims:
            # מתמודד עם רווחים או אותיות גדולות/קטנות
            if (c.get('class_type') or "").strip().lower() == c_name.lower():
                return c
        return None

    eco = find_dim("Economy")
    bus = find_dim("Business")
    size = (plane_details.get('size') or "").strip()

    if size == 'Small':
        if not eco:
            raise ValueError("Small plane must have Economy dimensions in classes table.")
        return SmallPlane(plane_details['id_plane'], plane_details['manufacturer'],
                          plane_details['purchase_date'], eco['num_rows'], eco['num_cols'])
    else: # Large
        if not eco or not bus:
            raise ValueError("Big plane must have BOTH Economy and Business dimensions in classes table.")
        return BigPlane(plane_details['id_plane'], plane_details['manufacturer'],
                        plane_details['purchase_date'], eco['num_rows'], eco['num_cols'],
                        bus['num_rows'], bus['num_cols'])

#Converts a list of occupied seats into a dictionary for fast lookup
def map_occupied_seats(occupied_list):
    occupied_map = {'Business': set(), 'Economy': set()}
    for item in occupied_list or []:
        c_type = (item.get('class_type') or "").strip()
        key = "Business" if c_type.lower() == "business" else "Economy"
        row = int(item['row_number'])
        letter = (item.get('seat_letter') or "").strip().upper()
        occupied_map[key].add((row, letter))
    return occupied_map

#Validates the selected seats against the current occupied seats for the flight and returns any conflicts
def validate_seat_selection(selected_seats, flight_id):
    current_occupied = db.get_occupied_seats(flight_id)
    occupied_map = map_occupied_seats(current_occupied)
    conflicts = []
    for seat_str in selected_seats:
        try:
            parts = seat_str.split('-')  # Business-1-A
            c_type = parts[0]
            row = int(parts[1])
            letter = parts[2]

            if (row, letter) in occupied_map.get(c_type, set()):
                conflicts.append(f"{c_type} row{row} seat{letter}")
        except:
            continue
    return conflicts

#Calculates the next booking ID based on the last ID stored in the database
def calculate_next_booking_id(last_id_from_db):
    if last_id_from_db is None:
        return 1001
    return last_id_from_db + 1
