from database import Database
from datetime import datetime, timedelta
from utils import prepare_flights_for_view, _format_datetime

db = Database()

# --- Section 1: Booking Lifecycle ---
class Flight:
    def __init__(self, flight_id):
        data = db.get_flight_data(flight_id=flight_id)
        if data:
            flight_info = data[0] if isinstance(data, list) else data
            self.id = flight_info['id_flight']
            self.departure_time = flight_info['departure_time']
            self.origin = flight_info['origin_city']
            self.destination = flight_info['destination_city']
            self._plane_info = None
        else:
            raise ValueError(f"Flight ID {flight_id} not found.")

    """Searches for available flights based on the specified date and route, formatting the raw database results for the application's view"""
    @staticmethod
    def search(date, origin, destination):
        raw_flights = db.get_flight_data(date_str=date, origin=origin, destination=destination)
        return prepare_flights_for_view(raw_flights)

"""Represents an aircraft entity, storing manufacturer details and providing methods to retrieve seat-map dimensions for specific cabin classes"""
class Plane:
    def __init__(self, id_plane, manufacturer, purchase_date):
        self.id_plane = id_plane
        self.manufacturer = manufacturer
        self.purchase_date = purchase_date
        self.dimensions = {"Business": None, "Economy": None}

    def has_class(self, class_type: str) -> bool:
        return self.dimensions.get(class_type) is not None

    def rows_cols(self, class_type: str):
        d = self.dimensions.get(class_type)
        if not d:
            return 0, 0
        return int(d["rows"]), int(d["cols"])

"""Specialized subclasses that extend the base Plane class to implement specific seat-map configurations for single-class (Small) and dual-class (Big) aircraft"""
class SmallPlane(Plane):
    def __init__(self, id_plane, manufacturer, purchase_date, eco_rows, eco_cols):
        super().__init__(id_plane, manufacturer, purchase_date)
        self.dimensions["Economy"] = {"rows": int(eco_rows), "cols": int(eco_cols)}
        self.dimensions["Business"] = None

class BigPlane(Plane):
    def __init__(self, id_plane, manufacturer, purchase_date, eco_rows, eco_cols, bus_rows, bus_cols):
        super().__init__(id_plane, manufacturer, purchase_date)
        self.dimensions["Economy"] = {"rows": int(eco_rows), "cols": int(eco_cols)}
        self.dimensions["Business"] = {"rows": int(bus_rows), "cols": int(bus_cols)}

# --- Section 2: User Authentication ---
"""A foundational class that represents all system participants, storing core identity data and providing property methods to verify user roles and access levels"""
class User:
    def __init__(self, role="guest", user_id=None, first_name=None, last_name=None, phone_numbers=None):
        self.role = role
        self.user_id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.phone_numbers = phone_numbers if phone_numbers else []

    @property
    def is_guest(self):
        return self.role == "guest"

    @property
    def is_registered(self):
        return self.role == "registered"

    @property
    def is_manager(self):
        return self.role == "manager"

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.first_name}>"

# --- Section 3: Customer Actions ---
class Guest(User):
    def __init__(self):
        super().__init__(role="guest")

class Customer(User):
    def __init__(self, email, first_name, last_name=None, passport=None, phone_numbers=None, date_of_birth=None):
        super().__init__(
            role="registered",
            user_id=email,
            first_name=first_name,
            last_name=last_name,
            phone_numbers=phone_numbers
        )
        self.passport = passport
        self.date_of_birth = date_of_birth

    """Manages the customer lifecycle by handling secure registration and authentication, including specific validation for unique identifiers like email and passport numbers"""
    @property
    def email(self):
        return self.user_id

    @staticmethod
    def login(email, password):
        user_data = db.user_login(email, password)
        if user_data:
            return Customer(email=user_data['email'], first_name=user_data['first_name_eng'])
        return None

    @staticmethod
    def register(email, first_name, last_name, birth_date, passport, password, phone_numbers):
        if db.email_exists(email):
            return False, "Email already registered."
        if db.passport_exists(passport):
            return False, "Passport number already exists."
        try:
            db.create_account(email, first_name, last_name, birth_date, passport, password, phone_numbers)
            return True, "Success"
        except Exception as e:
            return False, str(e)

class Booking:
    """Consolidates raw ticket data into organized booking records and categorizes them by status and departure time to provide a structured itinerary history for the user"""
    @staticmethod
    def get_user_bookings(email):
        raw_data = db.get_customer_bookings(email)
        bookings_dict = {}
        for row in raw_data:
            bid = row['id_booking']
            if bid not in bookings_dict:
                bookings_dict[bid] = {
                    'info': {
                        'id_booking': bid,
                        'booking_status': row['booking_status'],
                        'departure_time': row['departure_time'],
                        'origin': row['origin_city'],
                        'destination': row['destination_city'],
                        'total_price': row['total_price']
                    },
                    'tickets': []
                }
            bookings_dict[bid]['tickets'].append({
                'name': row['passenger_name'],
                'seat': f"{row['row_number']}{row['seat_letter']}",
                'class': row['class_type']
            })
        now = datetime.now()
        confirmed, completed, cancelled_you, cancelled_sys = [], [], [], []
        for b in bookings_dict.values():
            status = b['info']['booking_status']
            dep_time = b['info']['departure_time']
            if status == 'Confirmed':
                if dep_time > now:
                    confirmed.append(b)
                else:
                    completed.append(b)
            elif status == 'Completed':
                completed.append(b)
            elif status == 'Cancelled_Client':
                cancelled_you.append(b)
            elif status == 'Cancelled_System':
                cancelled_sys.append(b)
        return confirmed, completed, cancelled_you, cancelled_sys

    """Retrieves the complete details of a single booking by its unique ID and associated email, returning a structured record or None if no match is found"""
    @staticmethod
    def get_specific_booking(email, booking_id):
        raw_data = db.get_single_booking(email, booking_id)
        if not raw_data:
            return None

        booking = {
            'info': {
                'id_booking': raw_data[0]['id_booking'],
                'booking_status': raw_data[0]['booking_status'],
                'departure_time': raw_data[0]['departure_time'],
                'origin': raw_data[0]['origin_city'],
                'destination': raw_data[0]['destination_city'],
                'total_price': raw_data[0]['total_price']
            },
            'tickets': []
        }
        for row in raw_data:
            booking['tickets'].append({
                'name': row['passenger_name'],
                'seat': f"{row['row_number']}{row['seat_letter']}",
                'class': row['class_type']
            })
        return booking

    @staticmethod
    def organize_bookings(bookings_list):
        confirmed, completed, cancelled_you, cancelled_sys = [], [], [], []
        now = datetime.now()

        for b in bookings_list:
            info = b.get('info', b)

            status = info.get('booking_status')
            dep_time = info.get('departure_time')
            if isinstance(dep_time, str):
                try:
                    dep_time = datetime.strptime(dep_time, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass
            if status == 'Confirmed':
                if dep_time and dep_time > now:
                    confirmed.append(b)
                else:
                    completed.append(b)
            elif status == 'Completed':
                completed.append(b)
            elif status == 'Cancelled_Client':
                cancelled_you.append(b)
            elif status == 'Cancelled_System':
                cancelled_sys.append(b)

        return confirmed, completed, cancelled_you, cancelled_sys

    @staticmethod
    def cancel_by_customer(booking_id):
        """Customer cancellation: 36-hour check and penalty calculation"""
        flight_data = db.get_booking_details_for_cancellation(booking_id)

        if not flight_data:
            return False, "Booking not found."
        if 'Cancelled' in flight_data['status']:
            return False, "This booking is already cancelled."
        flight_time = flight_data['departure_time']
        time_diff = flight_time - datetime.now()
        if time_diff < timedelta(hours=36):
            return False, "Too late to cancel (under 36 hours before departure)."
        original_price = float(flight_data['total_price'])
        fee = original_price * 0.05
        success = db.update_booking_status(booking_id, 'Cancelled_Client', fee)
        if success:
            return True, f"Booking cancelled. A 5% fee (${fee:.2f}) was charged."
        return False, "Database update failed."

# --- Section 4: Management ---
class Manager(User):
    def __init__(self, id_worker, first_name):
        super().__init__(user_id=id_worker, first_name=first_name)
        self.id_worker = id_worker

    """Authenticates administrative credentials by verifying the worker ID and password, returning a Manager instance upon successful verification to initialize a secure session"""
    @staticmethod
    def login(id_worker, password):
        manager_data = db.manager_login(id_worker, password)
        if manager_data:
            return Manager(id_worker=manager_data['id_worker'], first_name=manager_data['first_name'])
        return None

    """Triggers the comprehensive administrative cancellation logic, which updates the flight status in the system and automatically manages the cancellation of all associated passenger bookings"""
    @staticmethod
    def cancel_flight(flight_id):
        return db.cancel_flight_full_logic(flight_id)

    @staticmethod
    def validate_resources(dept_time, route_id):
        result = db.get_available_resources(dept_time, route_id)
        if not result:
            return None
        v_planes = [p for p in result.get('planes', []) if p.get('is_valid')]
        v_pilots = [p for p in result.get('pilots', []) if p.get('is_valid')]
        v_attendants = [a for a in result.get('attendants', []) if a.get('is_valid')]
        is_long = result.get('is_long_haul', False)
        min_pilots = 3 if is_long else 2
        min_attendants = 6 if is_long else 3
        has_plane = len(v_planes) > 0
        has_pilots = len(v_pilots) >= min_pilots
        has_attendants = len(v_attendants) >= min_attendants

        can_proceed = has_plane and has_pilots and has_attendants

        error_msg = ""
        if not can_proceed:
            if not has_plane:
                error_msg = "No available aircraft (or the aircraft is too small for a long-haul flight)"
            elif not has_pilots:
                error_msg = f"Pilot shortage. Required {min_pilots}, found available: {len(v_pilots)}."
            else:
                error_msg = f"Pilot shortage. Required {min_attendants}, found available: {len(v_attendants)}."

        return {
            "can_proceed": can_proceed,
            "error_msg": error_msg,
            "planes": result.get('planes', []),
            "pilots": result.get('pilots', []),
            "attendants": result.get('attendants', []),
            "is_long_haul": is_long,
            "arrival_time": result.get('arrival_time', "N/A")
        }

    """Evaluates the operational feasibility of a proposed flight by verifying the availability of aircraft and crew, ensuring that staffing levels meet specific safety and regulatory requirements based on flight duration"""
    @staticmethod
    def get_dashboard_data():
        flights = db.get_all_flights_for_manager()
        now = datetime.now()
        for f in flights:
            f['formatted_date'] = _format_datetime(f['departure_time'])
            crew = db.get_flight_crew_names(f['id_flight'])
            f['pilots_list'] = ", ".join(crew['pilots'])
            f['attendants_list'] = ", ".join(crew['attendants'])
            time_diff = f['departure_time'] - now
            f['can_cancel'] = (f['flight_status'] == 'Scheduled' and
                               time_diff.total_seconds() > 72 * 3600)

        routes = db.get_routes_only()
        return flights, routes

    """Finalizes the flight scheduling process by committing the selected route, aircraft, and assigned crew members to the database while establishing the pricing structure for all cabin classes"""
    @staticmethod
    def create_flight(route_id, plane_id, departure_time, pilots_list, attendants_list,
                      manager_id, price_economy, price_business):  # <--- הוספנו פרמטרים
        return db.add_new_flight(
            route_id, plane_id, departure_time, pilots_list, attendants_list, manager_id,
            price_economy, price_business
        )

    """Retrieves a complete inventory of all operational assets—including pilots, flight attendants, and aircraft—providing a centralized data source for administrative management"""
    @staticmethod
    def get_all_resources():
        return {
            'pilots': db.get_all_pilots(),
            'attendants': db.get_all_flight_attendants(),
            'planes': db.get_all_planes()
        }

    """Facilitates the expansion of operational capacity by processing form-submitted data to register new pilots, flight attendants, or aircraft within the organizational database"""
    @staticmethod
    def add_new_resource(resource_type, form_data):
        return db.add_resource(resource_type, form_data)

    """Enables the modification of existing airline assets by processing updated form data to refresh pilot, attendant, or aircraft records within the centralized database"""
    @staticmethod
    def update_existing_resource(resource_type, form_data):
        return db.update_resource(resource_type, form_data)

    """Identifies and retrieves a specific operational resource by its unique identifier, enabling precise data editing and record management within the administrative interface"""
    @staticmethod
    def get_single_resource(resource_type, resource_id):
        all_data = Manager.get_all_resources()
        target_list = all_data.get(resource_type + 's', [])
        if resource_type == 'aircraft':
            target_list = all_data.get('planes', [])

        id_key = 'id_plane' if resource_type == 'aircraft' else 'id_worker'
        for item in target_list:
            if str(item[id_key]) == str(resource_id):
                return item
        return None
