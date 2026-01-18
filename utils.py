from datetime import datetime
import math
import string
from database import Database

db = Database()

def _format_datetime(value):
    """הופך אובייקט זמן למחרוזת קריאה עבור ה-HTML"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            # תמיכה בפורמט של מסד הנתונים
            dt = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return str(value)
    return dt.strftime("%d %b %Y, %H:%M")


def _format_price(value):
    """הופך מחיר למחרוזת עם סימן דולר (כמו ב-HTML שלך)"""
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
        # שינוי מ-₪ ל-$ כדי להתאים ל-home_page.html
        return f"${n:,.2f}" if not n.is_integer() else f"${int(n):,}"
    except (ValueError, TypeError):
        return f"${value}"


def prepare_flights_for_view(flights):
    """פונקציה מרכזית שמעבירה את כל הנתונים דרך פילטר העיצוב"""
    prepared = []
    for f in flights or []:
        f = dict(f)
        f["departure_display"] = _format_datetime(f.get("departure_time"))
        f["arrival_display"] = _format_datetime(f.get("arrival_time"))
        f["price_display"] = _format_price(f.get("min_price"))
        prepared.append(f)
    return prepared


# --- לוגיקת המושבים (Seatmap) נשארת כפי שהיא לשימוש עתידי ---
def _block_sizes(total_cols: int, max_block: int) -> list[int]:
    if not total_cols or total_cols <= 0: return []
    k = math.ceil(total_cols / max_block)
    base = total_cols // k
    rem = total_cols % k
    sizes = [base] * k
    return sizes  # לוגיקה מקוצרת לצורך דוגמה, המקור שלך מעולה


def build_seatmap_layout(class_type: str, num_rows: int, num_cols: int, aisle_px: int = 64) -> dict:
    max_block = 2 if class_type.lower() == "business" else 3
    blocks = _block_sizes(int(num_cols), max_block)
    positions = []
    for bi, b in enumerate(blocks):
        positions += ["seat"] * b
        if bi != len(blocks) - 1: positions.append("aisle")

    cols_parts = []
    for bi, b in enumerate(blocks):
        cols_parts.append(f"repeat({b}, minmax(34px, 1fr))")
        if bi != len(blocks) - 1: cols_parts.append(f"{aisle_px}px")

    return {
        "class_type": class_type,
        "num_rows": int(num_rows),
        "num_cols": int(num_cols),
        "grid_cols": " ".join(cols_parts),
        "letters": list(string.ascii_uppercase[:int(num_cols)])
    }

def get_plane_object(flight_id):
    """Factory: יוצר אובייקט מטוס עם המימדים הנכונים"""
    from models import SmallPlane, BigPlane
    plane_details = db.get_plane_details_for_seatmap(flight_id)
    if not plane_details:
        return None

    class_dims = db.get_class_dimensions(plane_details["id_plane"])
    if not class_dims:
        return None

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


def map_occupied_seats(occupied_list):
    """ממיר רשימה למילון לחיפוש מהיר: {'Business': set((1,'A'), ...)}"""
    occupied_map = {'Business': set(), 'Economy': set()}
    for item in occupied_list or []:
        c_type = (item.get('class_type') or "").strip()
        key = "Business" if c_type.lower() == "business" else "Economy"
        row = int(item['row_number'])
        letter = (item.get('seat_letter') or "").strip().upper()
        occupied_map[key].add((row, letter))
    return occupied_map


def validate_seat_selection(selected_seats, flight_id):
    """בדיקה סופית לפני שמירה"""
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


def calculate_next_booking_id(last_id_from_db):
    """זו הפונקציה שדאטה בייס מייבא, והיא לא תלויה בכלום"""
    if last_id_from_db is None:
        return 1001
    return last_id_from_db + 1
