import mysql.connector
from datetime import datetime, timedelta

class Database:
    _instance = None

    def __new__(cls):
        """Ensures a single global instance of the Database class by intercepting object creation and establishing a persistent connection to the 'flytau' schema"""
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            try:
                cls._instance.connection = mysql.connector.connect(
                    host="localhost",
                    user="root",
                    password="root",
                    database="flytau",
                    port=3306
                )
                print("✅ Connected to 'flytau' database (Singleton)")
            except mysql.connector.Error as err:
                print(f"❌ Connection Error: {err}")
                cls._instance.connection = None
        return cls._instance

# --- Section 1: Booking Lifecycle ---

    def get_all_destinations(self):
        """Retrieves a unique list of all available flight destinations, including city, country, and airport names, sorted alphabetically by city"""
        query = "SELECT DISTINCT city, country, airport_name FROM airports ORDER BY city"
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute(query)
        res = cursor.fetchall()
        cursor.close()
        return res

    def get_flight_data(self, date_str=None, origin=None, destination=None, flight_id=None):
        """dynamically filtering results based on date, origin, destination, or flight ID, while calculating arrival times and identifying the lowest available price"""
        cursor = self.connection.cursor(dictionary=True)
        query = """
            SELECT f.id_flight, 
                   f.departure_time, 
                   -- התיקון כאן: מחשבים את זמן ההגעה במקום לשלוף שדה שלא קיים
                   ADDTIME(f.departure_time, r.duration) AS arrival_time, 
                   f.flight_status,
                   a1.city as origin, 
                   a2.city as destination,
                   MIN(p.price) as min_price
            FROM flights f
            JOIN routes r ON f.id_route = r.id_route
            JOIN airports a1 ON r.origin_code = a1.airport_code
            JOIN airports a2 ON r.destination_code = a2.airport_code
            JOIN flight_pricing p ON f.id_flight = p.id_flight
        """
        params = []
        conditions = []
        if flight_id:
            conditions.append("f.id_flight = %s")
            params.append(flight_id)
        if date_str:
            conditions.append("DATE(f.departure_time) = %s")
            params.append(date_str)
        if origin:
            conditions.append("a1.city = %s")
            params.append(origin)
        if destination:
            conditions.append("a2.city = %s")
            params.append(destination)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " GROUP BY f.id_flight"

        cursor.execute(query, params)
        res = cursor.fetchall()
        cursor.close()
        return res

    def get_nearest_flight_date(self, origin, dest, target_date, after=False):
        """Retrieving the nearest flight date for a specific route"""
        operator = ">=" if after else "!="
        order_clause = "f.departure_time ASC" if after else f"ABS(DATEDIFF(f.departure_time, %s)) ASC"

        query = f"""
            SELECT DATE(f.departure_time) as flight_date
            FROM flights f
            JOIN routes r ON f.id_route = r.id_route
            JOIN airports a1 ON r.origin_code = a1.airport_code
            JOIN airports a2 ON r.destination_code = a2.airport_code
            WHERE a1.city = %s AND a2.city = %s 
            AND f.flight_status != 'Cancelled'
            AND DATE(f.departure_time) {operator} %s
            ORDER BY {order_clause} LIMIT 1
        """

        cursor = self.connection.cursor(dictionary=True)
        try:
            if not after:
                cursor.execute(query, (origin, dest, target_date, target_date))
            else:
                cursor.execute(query, (origin, dest, target_date))

            res = cursor.fetchone()
            return res['flight_date'].strftime('%Y-%m-%d') if res else None
        except Exception as e:
            print(f"Error in get_nearest_flight_date: {e}")
            return None
        finally:
            cursor.close()

    def get_plane_details_for_seatmap(self, flight_id):
        """Retrieving aircraft details and size by flight"""
        query = """
                SELECT p.id_plane, p.manufacturer, p.size, p.purchase_date
                FROM flights f
                         JOIN planes p on p.id_plane = f.id_plane
                WHERE f.id_flight = %s \
                """
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, (flight_id,))
            return cursor.fetchone()
        finally:
            cursor.close()

    def get_class_dimensions(self, plane_id):
        """Retrieving the number of rows and columns for each cabin class"""
        query = "SELECT class_type, num_rows, num_cols FROM classes WHERE id_plane = %s"
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, (plane_id,))
            return cursor.fetchall()
        finally:
            cursor.close()

    def get_flight_prices(self, flight_id):
        """Retrieving the flight price list"""
        query = ("SELECT class_type, price "
                 "FROM flight_pricing "
                 "WHERE id_flight = %s")
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, (flight_id,))
            res = cursor.fetchall()
            return {row['class_type']: float(row['price']) for row in res}
        finally:
            cursor.close()

    def get_occupied_seats(self, flight_id):
        """Retrieving occupied seats only"""
        query = """
                SELECT t.class_type, t.`row_number`, t.seat_letter
                FROM tickets t
                         JOIN bookings b ON b.id_booking = t.id_booking
                WHERE t.id_flight = %s \
                  AND b.status = 'confirmed'
                """
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, (flight_id,))
            return cursor.fetchall()
        finally:
            cursor.close()

    def create_new_booking(self, user_email, is_registered, total_price, flight_id, passengers):
        from utils import calculate_next_booking_id
        """"Processes flight bookings by validating flight data, handling guest or registered users, and committing booking and ticket records to the database"""

        print(f"--- STARTING BOOKING PROCESS FOR FLIGHT {flight_id} ---")
        cursor = self.connection.cursor()
        try:
            flight_id_int = int(flight_id)
            cursor.execute("SELECT id_plane FROM flights WHERE id_flight = %s", (flight_id_int,))
            plane_row = cursor.fetchone()
            if not plane_row:
                print(f"CRITICAL ERROR: Flight ID {flight_id_int} exists in session but NOT in DB!")
                raise Exception("Plane not found for this flight ID")
            if isinstance(plane_row, dict):
                plane_id = plane_row['id_plane']
            else:
                plane_id = plane_row[0]

            print(f"DEBUG: Found Plane ID: {plane_id}")

            if not is_registered:
                cursor.execute("SELECT email FROM customers WHERE email = %s", (user_email,))
                if not cursor.fetchone():
                    first_name = passengers[0]['first_name']
                    last_name = passengers[0]['last_name']
                    cursor.execute(
                        "INSERT INTO customers (email, first_name_eng, last_name_eng) VALUES (%s, %s, %s)",
                        (user_email, first_name, last_name)
                    )

                    contact_phones = passengers[0].get('contact_phone', [])
                    if not isinstance(contact_phones, list):
                        contact_phones = [contact_phones]
                    for phone in contact_phones:
                        if phone and str(phone).strip():
                            cursor.execute("INSERT INTO phone_numbers (phone_number, customers_email) VALUES (%s, %s)",
                                           (phone, user_email))

                cursor.execute("SELECT customers_email FROM guest_customers WHERE customers_email = %s", (user_email,))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO guest_customers (customers_email) VALUES (%s)", (user_email,))

            last_db_id = self.get_last_booking_id()
            new_booking_id = calculate_next_booking_id(last_db_id)

            reg_email_val = user_email if is_registered else None

            cursor.execute("""
                           INSERT INTO bookings (id_booking, customers_email, registered_email, booking_date, status,
                                                 total_price)
                           VALUES (%s, %s, %s, NOW(), 'Confirmed', %s)
                           """, (new_booking_id, user_email, reg_email_val, total_price))
            q_ticket = """
                       INSERT INTO tickets (id_booking, id_flight, passenger_name, passenger_passport,
                                            class_type, `row_number`, seat_letter, id_plane)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) 
                       """
            for p in passengers:
                full_name = f"{p['first_name']} {p['last_name']}"
                cursor.execute(q_ticket, (
                    new_booking_id, flight_id_int, full_name, p['passport'],
                    p['class_type'], p['row_number'], p['seat_letter'], plane_id))


            self.connection.commit()
            print("--- BOOKING SUCCESSFUL ---")
            return True, new_booking_id

        except Exception as e:
            self.connection.rollback()
            print(f"Error creating booking transaction: {e}")
            return False, str(e)
        finally:
            cursor.close()

# --- Section 2: User Authentication ---

    def user_login(self, email, password):
        """verifying credentials against the database and retrieving profile details for session initialization"""
        query = """
            SELECT c.email, c.first_name_eng 
            FROM customers c
            JOIN registered_customers rc ON c.email = rc.customers_email
            WHERE c.email = %s AND rc.password = %s
        """
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, (email, password))
            result = cursor.fetchone()
            return result
        finally:
            cursor.close()

    def manager_login(self, id_worker, password):
        """validating manager credentials and retrieving profile information for authorized sessions"""
        query = "SELECT id_worker, first_name FROM managers WHERE id_worker = %s AND password = %s"
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute(query, (id_worker, password))
        result = cursor.fetchone()
        cursor.close()
        return result

    def create_account(self, email, first_name, last_name, birth_date, passport, password, phone_numbers):
        """managing customer profiles, upgrading guest records to registered status, and ensuring data integrity through secure database transactions"""
        cursor = self.connection.cursor()
        try:
            try:
                cursor.execute("""
                               INSERT INTO customers (email, first_name_eng, last_name_eng)
                               VALUES (%s, %s, %s)
                               """, (email, first_name, last_name))

            except mysql.connector.Error as err:
                if err.errno == 1062:
                    cursor.execute("SELECT customers_email FROM registered_customers WHERE customers_email = %s",
                                   (email,))
                    if cursor.fetchone():
                        raise Exception("Email already registered.")
                    else:
                        cursor.execute("""
                                       UPDATE customers
                                       SET first_name_eng = %s,
                                           last_name_eng  = %s
                                       WHERE email = %s
                                       """, (first_name, last_name, email))
                else:
                    raise err

            cursor.execute("""
                           INSERT INTO registered_customers (customers_email, password, birth_date, passport, registration_date)
                           VALUES (%s, %s, %s, %s, CURDATE())
                           """, (email, password, birth_date, passport))

            for phone in phone_numbers:
                if phone and str(phone).strip():
                    cursor.execute("""
                                   INSERT INTO phone_numbers (phone_number, customers_email)
                                   VALUES (%s, %s)
                                   """, (phone, email))

            self.connection.commit()
            return True, "Registration successful"

        except Exception as e:
            self.connection.rollback()
            error_msg = str(e)
            if "Duplicate entry" in error_msg and "passport" in error_msg:
                return False, "This passport number is already registered."
            return False, f"Registration failed: {error_msg}"
        finally:
            cursor.close()

    def email_exists(self, email):
        """Checks the database to verify if a specific email address is already associated with a registered customer account"""
        cursor = self.connection.cursor()
        cursor.execute("SELECT customers_email FROM registered_customers WHERE customers_email = %s", (email,))
        exists = cursor.fetchone() is not None
        cursor.close()
        return exists

    def passport_exists(self, passport):
        """Validates the uniqueness of a passport number by checking for its existence within the registered customers database"""
        cursor = self.connection.cursor()
        cursor.execute("SELECT passport FROM registered_customers WHERE passport = %s", (passport,))
        exists = cursor.fetchone() is not None
        cursor.close()
        return exists

# --- Section 3: Customer Actions ---

    def get_single_booking(self, email, booking_id):
        """Fetches detailed information for a specific booking ID linked to either customer or registered email."""
        query = """
            SELECT b.id_booking, b.status as booking_status, b.total_price,
                   f.departure_time, a1.city as origin_city, a2.city as destination_city,
                   t.passenger_name, t.seat_letter, t.`row_number`, t.class_type
            FROM bookings b
            JOIN tickets t ON b.id_booking = t.id_booking
            JOIN flights f ON t.id_flight = f.id_flight
            JOIN routes r ON f.id_route = r.id_route
            JOIN airports a1 ON r.origin_code = a1.airport_code
            JOIN airports a2 ON r.destination_code = a2.airport_code
            WHERE (b.customers_email = %s OR b.registered_email = %s) AND b.id_booking = %s
        """
        cursor = self.connection.cursor(dictionary=True)
        try:
            # We pass the email twice to check both potential columns
            cursor.execute(query, (email.strip(), email.strip(), str(booking_id).strip()))
            return cursor.fetchall()
        finally:
            cursor.close()

    def get_customer_bookings(self, email):
        """Retrieves a comprehensive booking history for a specific registered user, aggregating flight schedules, route information, and individual ticket details into a single, chronologically ordered result set"""
        query = """
            SELECT 
                b.id_booking, b.booking_date, b.status as booking_status, b.total_price,
                f.id_flight, f.departure_time,
                r.origin_code, a1.city as origin_city,
                r.destination_code, a2.city as destination_city,
                t.passenger_name, t.passenger_passport, t.seat_letter, t.`row_number`, t.class_type
            FROM bookings b
            JOIN tickets t ON b.id_booking = t.id_booking
            JOIN flights f ON t.id_flight = f.id_flight
            JOIN routes r ON f.id_route = r.id_route
            JOIN airports a1 ON r.origin_code = a1.airport_code
            JOIN airports a2 ON r.destination_code = a2.airport_code
            WHERE b.registered_email = %s
            ORDER BY f.departure_time DESC
        """
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, (email,))
            return cursor.fetchall()
        finally:
            cursor.close()

    def get_booking_details_for_cancellation(self, booking_id):
        """Retrieves essential flight departure and pricing data through a multi-table join to validate cancellation eligibility and calculate potential penalties"""
        query = """
            SELECT f.departure_time, b.total_price, b.status 
            FROM bookings b 
            JOIN tickets t ON b.id_booking = t.id_booking 
            JOIN flights f ON t.id_flight = f.id_flight 
            WHERE b.id_booking = %s 
            LIMIT 1
        """
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query, (booking_id,))
            return cursor.fetchone()
        finally:
            cursor.close()

    def update_booking_status(self, booking_id, new_status, new_price):
        """Updates the status and total price of a specific booking while ensuring data integrity through a secured database transaction"""
        query = "UPDATE bookings SET status = %s, total_price = %s WHERE id_booking = %s"
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, (new_status, new_price, booking_id))
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            return False
        finally:
            cursor.close()

# --- Section 4: Management ---

    def get_all_flights_for_manager(self):
        """aggregating flight schedules, aircraft specifications, and real-time passenger counts through complex multi-table joins and correlated subqueries"""
        query = """
            SELECT 
                f.id_flight, 
                f.departure_time, 
                ADDTIME(f.departure_time, r.duration) as landing_time,
                f.flight_status,
                r.origin_code, 
                air_origin.country as origin_country,
                r.destination_code, 
                air_dest.country as destination_country,
                p.id_plane, 
                p.size as plane_size,
                (SELECT COUNT(*) FROM tickets t WHERE t.id_flight = f.id_flight) as passenger_count
            FROM flights f
            JOIN routes r ON f.id_route = r.id_route
            JOIN planes p ON f.id_plane = p.id_plane
            JOIN airports air_origin ON r.origin_code = air_origin.airport_code
            JOIN airports air_dest ON r.destination_code = air_dest.airport_code
            ORDER BY f.departure_time DESC
        """
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute(query)
        res = cursor.fetchall()
        cursor.close()
        return res

    def get_flight_crew_names(self, flight_id):
        """Retrieving full names of the assigned flight crew"""
        cursor = self.connection.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT CONCAT(first_name, ' ', last_name) AS full_name 
                FROM pilots p
                JOIN pilots_in_flights pif ON p.id_worker = pif.id_worker
                WHERE pif.id_flight = %s""", (flight_id,))
            pilots = [row['full_name'] for row in cursor.fetchall()]

            cursor.execute("""
                SELECT CONCAT(first_name, ' ', last_name) AS full_name 
                FROM flight_attendants fa
                JOIN flight_attendants_in_flights af ON fa.id_worker = af.id_worker
                WHERE af.id_flight = %s""", (flight_id,))
            attendants = [row['full_name'] for row in cursor.fetchall()]

            return {"pilots": pilots, "attendants": attendants}

        except Exception as e:
            print(f"Error getting crew: {e}")
            return {"pilots": [], "attendants": []}

        finally:
            cursor.close()

    def cancel_flight_full_logic(self, flight_id):
        cursor = self.connection.cursor()
        try:
            cursor.execute("UPDATE flights SET flight_status = 'Cancelled' WHERE id_flight = %s", (flight_id,))
            cursor.execute(
                "UPDATE bookings b JOIN tickets t ON b.id_booking = t.id_booking SET b.status = 'Cancelled_System' WHERE t.id_flight = %s",
                (flight_id,))
            self.connection.commit()
            return True, "Flight cancelled successfully."
        except Exception as e:
            self.connection.rollback()
            return False, str(e)
        finally:
            cursor.close()

    def get_routes_only(self):
        """Retrieving all existing routes to populate the dashboard form"""
        cursor = self.connection.cursor(dictionary=True)
        query = """
            SELECT r.id_route, 
                   r.origin_code, a1.city as origin_city, 
                   r.destination_code, a2.city as destination_city,
                   r.duration
            FROM routes r
            JOIN airports a1 ON r.origin_code = a1.airport_code
            JOIN airports a2 ON r.destination_code = a2.airport_code
        """
        cursor.execute(query)
        result = cursor.fetchall()
        cursor.close()
        return result

    def get_available_resources(self, departure_time_str, route_id):
        """Verifying availability based on location, credentials, and time overlap"""
        cursor = self.connection.cursor(dictionary=True)
        try:
            clean_time_str = departure_time_str.replace('T', ' ')
            if len(clean_time_str) == 16: clean_time_str += ':00'
            dep_time = datetime.strptime(clean_time_str, '%Y-%m-%d %H:%M:%S')

            cursor.execute("SELECT origin_code, duration FROM routes WHERE id_route = %s", (route_id,))
            route = cursor.fetchone()
            if not route: return None

            required_origin = route['origin_code']
            duration_str = str(route['duration'])
            h, m, s = map(int, duration_str.split(':'))
            arr_time = dep_time + timedelta(hours=h, minutes=m, seconds=s)

            total_minutes = (h * 60) + m
            is_long_haul = total_minutes > 360

            query_planes = """
                SELECT p.id_plane, p.size,
                COALESCE(
                    (SELECT r_prev.destination_code 
                     FROM flights f_prev 
                     JOIN routes r_prev ON f_prev.id_route = r_prev.id_route
                     WHERE f_prev.id_plane = p.id_plane 
                       AND f_prev.departure_time < %s 
                       AND f_prev.flight_status != 'Cancelled'
                     ORDER BY f_prev.departure_time DESC LIMIT 1
                    ), 'TLV') as current_location,
                (SELECT COUNT(*) FROM flights f
                 JOIN routes r ON f.id_route = r.id_route
                 WHERE f.id_plane = p.id_plane
                   AND (f.departure_time < %s) 
                   AND (ADDTIME(f.departure_time, r.duration) > %s)
                   AND f.flight_status != 'Cancelled'
                ) as busy_count
                FROM planes p
            """
            cursor.execute(query_planes, (dep_time, arr_time, dep_time))
            planes_raw = cursor.fetchall()

            processed_planes = []
            for p in planes_raw:
                is_busy = p['busy_count'] > 0
                loc_ok = (p['current_location'] == required_origin)
                size_ok = not (is_long_haul and p['size'] != 'Large')

                reason = ""
                if is_busy:
                    reason = "Time Overlap (Busy)"
                elif not loc_ok:
                    reason = f"Located in {p['current_location']}"
                elif not size_ok:
                    reason = "Plane too small"

                if not size_ok and is_long_haul: continue

                processed_planes.append({
                    'id_plane': p['id_plane'], 'size': p['size'], 'current_location': p['current_location'],
                    'is_valid': (not is_busy) and loc_ok, 'reason': reason
                })

            query_pilots = """
                SELECT w.id_worker, w.first_name, w.last_name, w.long_flights,
                COALESCE(
                    (SELECT r_prev.destination_code 
                     FROM flights f_prev 
                     JOIN routes r_prev ON f_prev.id_route = r_prev.id_route
                     JOIN pilots_in_flights pif ON f_prev.id_flight = pif.id_flight
                     WHERE pif.id_worker = w.id_worker
                       AND f_prev.departure_time < %s 
                       AND f_prev.flight_status != 'Cancelled'
                     ORDER BY f_prev.departure_time DESC LIMIT 1
                    ), 'TLV') as current_location,
                (SELECT COUNT(*) FROM pilots_in_flights pf
                 JOIN flights f ON pf.id_flight = f.id_flight
                 JOIN routes r ON f.id_route = r.id_route
                 WHERE pf.id_worker = w.id_worker
                   AND (f.departure_time < %s) 
                   AND (ADDTIME(f.departure_time, r.duration) > %s)
                   AND f.flight_status != 'Cancelled'
                ) as busy_count
                FROM pilots w
            """
            cursor.execute(query_pilots, (dep_time, arr_time, dep_time))
            pilots_raw = cursor.fetchall()

            processed_pilots = []
            for w in pilots_raw:
                is_busy = w['busy_count'] > 0
                loc_ok = (w['current_location'] == required_origin)
                qual_ok = not (is_long_haul and w['long_flights'] == 0)

                reason = ""
                if is_busy:
                    reason = "Time Overlap"
                elif not loc_ok:
                    reason = f"Located in {w['current_location']}"
                elif not qual_ok:
                    reason = "Not Qualified"

                processed_pilots.append({
                    'id_worker': w['id_worker'], 'name': f"{w['first_name']} {w['last_name']}",
                    'qualified_for_long_haul': (w['long_flights'] == 1),
                    'is_valid': (not is_busy) and loc_ok and qual_ok, 'reason': reason
                })

            query_attendants = """
                SELECT w.id_worker, w.first_name, w.last_name, w.long_flights,
                COALESCE(
                    (SELECT r_prev.destination_code 
                     FROM flights f_prev 
                     JOIN routes r_prev ON f_prev.id_route = r_prev.id_route
                     JOIN flight_attendants_in_flights af ON f_prev.id_flight = af.id_flight
                     WHERE af.id_worker = w.id_worker
                       AND f_prev.departure_time < %s 
                       AND f_prev.flight_status != 'Cancelled'
                     ORDER BY f_prev.departure_time DESC LIMIT 1
                    ), 'TLV') as current_location,
                (SELECT COUNT(*) FROM flight_attendants_in_flights af
                 JOIN flights f ON af.id_flight = f.id_flight
                 JOIN routes r ON f.id_route = r.id_route
                 WHERE af.id_worker = w.id_worker
                   AND (f.departure_time < %s) 
                   AND (ADDTIME(f.departure_time, r.duration) > %s)
                   AND f.flight_status != 'Cancelled'
                ) as busy_count
                FROM flight_attendants w
            """
            cursor.execute(query_attendants, (dep_time, arr_time, dep_time))
            attendants_raw = cursor.fetchall()

            processed_attendants = []
            for w in attendants_raw:
                is_busy = w['busy_count'] > 0
                loc_ok = (w['current_location'] == required_origin)
                qual_ok = not (is_long_haul and w['long_flights'] == 0)

                reason = ""
                if is_busy:
                    reason = "Time Overlap"
                elif not loc_ok:
                    reason = f"Located in {w['current_location']}"
                elif not qual_ok:
                    reason = "Not Qualified"

                processed_attendants.append({
                    'id_worker': w['id_worker'], 'name': f"{w['first_name']} {w['last_name']}",
                    'qualified_for_long_haul': (w['long_flights'] == 1),
                    'is_valid': (not is_busy) and loc_ok and qual_ok, 'reason': reason
                })

            return {
                "planes": processed_planes, "pilots": processed_pilots, "attendants": processed_attendants,
                "is_long_haul": is_long_haul, "arrival_time": arr_time.strftime('%Y-%m-%d %H:%M')
            }

        except Exception as e:
            print(f"Error checking availability: {e}")
            return None
        finally:
            cursor.close()

    def add_new_flight(self, route_id, plane_id, departure_time, pilots_ids, attendants_ids, manager_id, price_eco,
                       price_bus):
        """Creating a new flight, assigning the crew, and setting prices"""
        cursor = self.connection.cursor()
        try:
            query_flight = """
                INSERT INTO flights (id_route, id_plane, departure_time, flight_status, managers_id_worker)
                VALUES (%s, %s, %s, 'Scheduled', %s)
            """
            cursor.execute(query_flight, (route_id, plane_id, departure_time, manager_id))
            new_flight_id = cursor.lastrowid

            query_pilots = "INSERT INTO pilots_in_flights (id_worker, id_flight) VALUES (%s, %s)"
            for pid in pilots_ids:
                cursor.execute(query_pilots, (pid, new_flight_id))

            query_attendants = "INSERT INTO flight_attendants_in_flights (id_worker, id_flight) VALUES (%s, %s)"
            for aid in attendants_ids:
                cursor.execute(query_attendants, (aid, new_flight_id))

            cursor.execute(
                "INSERT INTO flight_pricing (id_flight, price, class_type) VALUES (%s, %s, 'Economy')",
                (new_flight_id, price_eco))

            if price_bus and str(price_bus).strip():
                cursor.execute(
                    "INSERT INTO flight_pricing (id_flight, price, class_type) VALUES (%s, %s, 'Business')",
                    (new_flight_id, price_bus))

            self.connection.commit()
            return True, "Flight created successfully"
        except Exception as e:
            self.connection.rollback()
            return False, str(e)
        finally:
            cursor.close()

    def add_resource(self, res_type, form):
        """A unified function for inserting aircraft and staff records into the database, including the dynamic generation of seat layouts for different cabin classes"""
        import string
        cursor = self.connection.cursor()
        try:
            if res_type == 'aircraft':
                id_p = form.get('id_plane')
                size = form.get('size')
                cursor.execute("""
                    INSERT INTO planes (id_plane, manufacturer, size, purchase_date)
                    VALUES (%s, %s, %s, %s)
                """, (id_p, form.get('manufacturer'), size, form.get('purchase_date')))
                class_configs = [('Economy', form.get('eco_rows'), form.get('eco_cols'))]
                if size == 'Large':
                    class_configs.append(('Business', form.get('bus_rows'), form.get('bus_cols')))

                for c_type, rows, cols in class_configs:
                    if not rows or not cols: continue

                    cursor.execute(
                        "INSERT INTO classes (class_type, num_rows, num_cols, id_plane) VALUES (%s, %s, %s, %s)",
                        (c_type, int(rows), int(cols), id_p))

                    letters = string.ascii_uppercase[:int(cols)]

                    for r in range(1, int(rows) + 1):

                        for l in letters:
                            cursor.execute(
                                "INSERT INTO seats (`row_number`, seat_letter, class_type, id_plane) VALUES (%s, %s, %s, %s)",
                                (r, l, c_type, id_p))

            elif res_type in ['pilot', 'attendant']:
                table = "pilots" if res_type == 'pilot' else "flight_attendants"
                long_haul = 1 if form.get('long_flights') else 0
                cursor.execute(f"""
                    INSERT INTO {table} 
                    (id_worker, first_name, last_name, phone_number, start_date, city, street, house_number, long_flights)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    form.get('id_worker'),
                    form.get('first_name'),
                    form.get('last_name'),
                    form.get('phone'),
                    form.get('start_date'),
                    form.get('city'),
                    form.get('street'),
                    form.get('house_number'),
                    long_haul
                ))

            self.connection.commit()
            return True
        except Exception as e:
            print(f"Error adding resource: {e}")
            self.connection.rollback()
            return False
        finally:
            cursor.close()

    def update_resource(self, res_type, form):
        """Modifies stored data for aircraft, pilots, or flight attendants by mapping form inputs to their respective database tables"""
        cursor = self.connection.cursor()
        try:
            if res_type == 'aircraft':
                cursor.execute("UPDATE planes SET manufacturer=%s WHERE id_plane=%s",
                               (form.get('manufacturer'), form.get('id_plane')))

            elif res_type in ['pilot', 'attendant']:
                table = "pilots" if res_type == 'pilot' else "flight_attendants"
                long_haul = 1 if form.get('long_flights') else 0
                cursor.execute(f"""
                    UPDATE {table} 
                    SET first_name=%s, last_name=%s, phone_number=%s, start_date=%s, 
                        city=%s, street=%s, house_number=%s, long_flights=%s
                    WHERE id_worker=%s
                """, (
                    form.get('first_name'),
                    form.get('last_name'),
                    form.get('phone'),
                    form.get('start_date'),
                    form.get('city'),
                    form.get('street'),
                    form.get('house_number'),
                    long_haul,
                    form.get('id_worker')
                ))

            self.connection.commit()
            return True
        except Exception as e:
            print(f"Error updating resource: {e}")
            self.connection.rollback()
            return False
        finally:
            cursor.close()


    def get_all_flight_attendants(self):
        """Retrieving flight attendants list"""
        query = """ SELECT * FROM flight_attendants """
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query)
            res = cursor.fetchall()
            return res
        finally:
            cursor.close()

    def get_all_pilots(self):
        """Retrieving pilots list"""
        query = """ SELECT * FROM pilots """
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query)
            res = cursor.fetchall()
            return res
        finally:
            cursor.close()

    def get_all_planes(self):
        """Retrieving planes list"""
        query = """ SELECT * FROM planes """
        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query)
            res = cursor.fetchall()
            return res
        finally:
            cursor.close()

# --- Section 5: Data & more ---

    def get_full_user_details(self, email):
        """Retrieving details for form auto-fill (for registered users)"""
        query_user = """ SELECT c.first_name_eng AS first_name,
                                c.last_name_eng  AS last_name,
                                c.email,
                                r.passport
                         FROM customers c
                                  JOIN registered_customers r ON c.email = r.customers_email
                         WHERE c.email = %s """

        query_phones = "SELECT phone_number FROM phone_numbers WHERE customers_email = %s"

        cursor = self.connection.cursor(dictionary=True)
        try:
            cursor.execute(query_user, (email,))
            user_data = cursor.fetchone()

            if user_data:
                cursor.execute(query_phones, (email,))
                phones = cursor.fetchall()
                user_data['phone_numbers'] = [p['phone_number'] for p in phones]
                return user_data
            return {}
        finally:
            cursor.close()


    def get_last_booking_id(self):
        """Helper function to retrieve the highest ID in a table"""
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT MAX(id_booking) FROM bookings")
            row = cursor.fetchone()
            return row[0]
        finally:
            cursor.close()
