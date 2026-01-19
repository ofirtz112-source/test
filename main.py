from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from models import Customer, Manager, Flight, Booking
from database import Database
from datetime import datetime, timedelta
from utils import get_plane_object, map_occupied_seats, validate_seat_selection, _format_price, prepare_flights_for_view

app = Flask(__name__)
app.secret_key = 'flytau_secret_key'
db = Database()

# --- Section 1: Booking Lifecycle ---

"""Handles the flight search engine logic and displays results or suggested dates on the main landing page"""
@app.route('/')
def home_page():
    destinations = db.get_all_destinations()

    origin = request.args.get('origin')
    destination = request.args.get('destination')
    date = request.args.get('date')
    return_date = request.args.get('return_date')
    trip_type = request.args.get('trip_type')

    outbound_flights = []
    return_flights = []
    suggested_dates = {"outbound": None, "return": None}
    search_performed = False

    if origin and destination and date:
        search_performed = True
        outbound_flights = Flight.search(date, origin, destination)

        if not outbound_flights:
            suggested_dates["outbound"] = db.get_nearest_flight_date(origin, destination, date)

        if trip_type == 'round' and return_date:
            return_flights = Flight.search(return_date, destination, origin)

            if not return_flights:
                # בודקים אחרי תאריך ההמראה (הלוך)
                base_date = suggested_dates["outbound"] if suggested_dates["outbound"] else date
                suggested_dates["return"] = db.get_nearest_flight_date(destination, origin, base_date, after=True)

    return render_template('home_page.html',
                           destinations=destinations,
                           outbound_flights=outbound_flights,
                           return_flights=return_flights,
                           suggested_dates=suggested_dates,
                           search_performed=search_performed,
                           origin=origin,
                           destination=destination,
                           date=date,
                           return_date=return_date,
                           trip_type=trip_type)

"""Displays the interactive seat map with real-time availability and class-based pricing for the selected flight"""
@app.route("/select-seats", methods=["GET"])
def select_seats_page():
    flight_id = request.args.get("flight_id")
    if not flight_id:
        return redirect(url_for('home_page'))

    raw_flight = db.get_flight_data(flight_id=flight_id)
    if not raw_flight:
        return redirect(url_for('home_page'))
    flight_view = prepare_flights_for_view(raw_flight)[0]
    plane = get_plane_object(flight_id)
    if not plane:
        flash("Plane configuration missing.", "error")
        return redirect(url_for('home_page'))
    occupied_map = map_occupied_seats(db.get_occupied_seats(flight_id))
    seats_prices = db.get_flight_prices(flight_id)
    formatted_prices = {k: _format_price(v) for k, v in seats_prices.items()}

    return render_template("select_seats.html",
                           flight=flight_view,
                           plane=plane,
                           occupied=occupied_map,
                           prices=formatted_prices,
                           col_letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ")

"""Validates seat availability and initializes the temporary booking record in the session"""
@app.route("/process-booking", methods=["POST"])
def process_booking():
    flight_id = request.form.get("flight_id")
    selected_seats = request.form.getlist("seats")
    if not selected_seats:
        flash("Please select at least one seat.", "error")
        return redirect(url_for('select_seats_page', flight_id=flight_id))
    conflicts = validate_seat_selection(selected_seats, flight_id)

    if conflicts:
        conflict_msg = ", ".join(conflicts)
        flash(f"Oops! The following seats were just taken: {conflict_msg}. Please choose different seats.")
        return redirect(url_for('select_seats_page', flight_id=flight_id))

    session['current_booking'] = {'flight_id': flight_id, 'seats': selected_seats}
    return redirect(url_for('passenger_details_page'))

"""Prepares the passenger details interface by calculating total costs and retrieving user profile data for automated form-filling"""
@app.route("/passenger-details", methods=["GET"])
def passenger_details_page():
    booking_data = session.get('current_booking')
    if not booking_data:
        return redirect(url_for('home_page'))

    flight_id = booking_data['flight_id']
    seats = booking_data['seats']
    raw_flight = db.get_flight_data(flight_id=flight_id)
    if not raw_flight:
        return redirect(url_for('home_page'))
    flight_view = prepare_flights_for_view(raw_flight)[0]
    prices_raw = db.get_flight_prices(flight_id)
    prices_normalized = {k.strip().lower(): v for k, v in prices_raw.items()}
    total_price = 0
    seats_list = []

    for s in seats:
        parts = s.split('-')
        class_from_html = parts[0]
        lookup_key = class_from_html.strip().lower()
        price = prices_normalized.get(lookup_key, 0)

        print(f"--- DEBUG: Seat '{s}' -> Key '{lookup_key}' -> Found Price: {price}")
        total_price += price
        display_class = class_from_html.capitalize()
        seats_list.append({'seat_code': f"{parts[1]}{parts[2]}", 'class': display_class, 'price': price})

    booking_data['total_price'] = total_price
    session['current_booking'] = booking_data
    total_seats = len(seats)
    num_forms = 2 if total_seats > 2 else total_seats
    user_details = {}
    if session.get('role') == 'customer' and session.get('email'):
        user_details = db.get_full_user_details(session['email'])

    return render_template("tickets_details.html",
                           flight=flight_view,
                           seats_list=seats_list,
                           total_price=total_price,
                           total_seats=total_seats,
                           num_forms=num_forms,
                           user=user_details)

"""Validates final seat availability and maps submitted passenger data from the form to the session-based booking object"""
@app.route("/save-passengers", methods=["POST"])
def save_passengers():
    current_booking = session.get('current_booking')
    if not current_booking:
        return redirect("/")

    flight_id = current_booking['flight_id']
    seats = current_booking['seats']
    conflicts = validate_seat_selection(seats, flight_id)
    if conflicts:
        conflict_msg = ", ".join(conflicts)
        flash(f"Oops! The following seats were just taken: {conflict_msg}. Please choose different seats.")
        session.pop('current_booking', None)
        return redirect(url_for('select_seats_page', flight_id=flight_id))
    f = request.form
    passengers_info = []
    prices = db.get_flight_prices(flight_id)
    total_final_price = 0

    for i, seat_str in enumerate(seats, 1):
        parts = seat_str.split('-')
        c_type = parts[0]
        row = parts[1]
        letter = parts[2]

        price = prices.get(c_type, 0)
        total_final_price += price

        if i <= 2:
            p_first_name = f.get(f'first_name_{i}')
            p_last_name = f.get(f'last_name_{i}')
            raw_passport = f.get(f'passport_{i}')
            p_passport = raw_passport.upper() if raw_passport else ""

        else:
            p_first_name = f.get('first_name_1')
            p_last_name = f.get('last_name_1')
            base_raw = f.get('passport_1')
            base_passport = base_raw.upper() if base_raw else ""
            p_passport = f"{base_passport}-{i}"
        p = {
            'seat_str': seat_str,
            'class_type': c_type,
            'row_number': row,
            'seat_letter': letter,
            'price': price,
            'first_name':p_first_name,
            'last_name': p_last_name,
            'passport': p_passport,
            'contact_phone': f.getlist('phone_numbers') if i == 1 else None,
            'contact_email': f.get('email_1') if i == 1 else None
        }
        passengers_info.append(p)

    current_booking['passengers'] = passengers_info
    current_booking['total_price'] = total_final_price
    session['current_booking'] = current_booking
    return redirect(url_for('booking_summery_page'))

"""Compiles and displays the final itinerary, passenger details, and total price for review before the transaction is finalized"""
@app.route("/booking-summery", methods=["GET"])
def booking_summery_page():
    booking_data = session.get('current_booking')
    if not booking_data:
        return redirect("/")

    flight_id = booking_data['flight_id']
    seat_strings = booking_data['seats']
    passengers = booking_data.get('passengers', [])  # <--- שליפת רשימת הנוסעים המוכנה
    raw_flight = db.get_flight_data(flight_id=flight_id)
    if raw_flight:
        flight = prepare_flights_for_view(raw_flight)[0]
    else:
        return redirect("/")
    seats_prices = db.get_flight_prices(flight_id)
    summary_rows = []
    total_price = 0.0

    for i, seat_str in enumerate(seat_strings, 1):
        parts = seat_str.split('-')
        class_type = parts[0]
        row = parts[1]
        letter = parts[2]

        price = seats_prices.get(class_type, 0.0)
        total_price += price

        full_name = "Guest"
        passport_number = ""
        if i <= len(passengers):
            p = passengers[i - 1]
            full_name = f"{p['first_name']} {p['last_name']}"
            passport_num = p.get('passport', '')

        summary_rows.append({
            "index": i,
            "passenger_name": full_name,
            "passport": passport_num,
            "seat_display": f"{class_type} - Row {row}, Seat {letter}",
            "class_type": class_type,
            "seat_code": f"{row}{letter}",
            "price_fmt": _format_price(price)
        })

    formatted_total = _format_price(total_price)

    return render_template("booking_payment.html",
                           flight=flight,
                           summary_rows=summary_rows,
                           total_price=formatted_total)

"""Finalizes the booking transaction by identifying the user type, committing the records to the database, and clearing the temporary session data"""
@app.route("/booking-payment", methods=["GET","POST"])
def booking_payment():

    if request.method == "GET":
        return redirect("/")

    booking_data = session.get('current_booking')
    if not booking_data: return redirect("/")

    passengers = booking_data['passengers']
    total_price = booking_data['total_price']
    flight_id = booking_data['flight_id']

    if session.get('role') == 'customer':
        user_email = session.get('email')
        is_registered = True
    else:
        user_email = passengers[0]['contact_email']
        is_registered = False

    success, booking_id = db.create_new_booking(user_email, is_registered, total_price, flight_id, passengers)

    if success:
        session.pop('current_booking', None)
        return redirect(url_for('booking_confirmation_page', booking_id=booking_id, email=user_email))
    else:
        flash("Error processing payment.", "error")
        return redirect(url_for('booking_summery_page'))

"""Displays the booking confirmation page with the unique booking ID and the user's email address"""
@app.route("/booking-confirmation/<int:booking_id>")
def booking_confirmation_page(booking_id):
    email = request.args.get('email', '')
    return render_template("booking_confirmation.html", booking_id=booking_id, email=email)

# --- Section 2: User Authentication ---

"""Manages customer authentication by verifying credentials and initializing a secure user session to enable personalized access"""
@app.route('/login', methods=['GET', 'POST'])
def register_login_page():
    email = None
    if request.method == 'POST':
        email = request.form.get('email').strip()
        password = request.form.get('password').strip()
        user = Customer.login(email, password)
        if user:
            session['user_id'] = user.email
            session['first_name'] = user.first_name
            session['role'] = 'customer'
            session['email'] = user.email
            return redirect(url_for('home_page'))
        flash("Invalid email or password", "danger")
    return render_template('register_login.html', email=email)

"""Manages new customer onboarding by processing registration data and immediately initializing a secure session upon successful account creation"""
@app.route('/register', methods=['GET', 'POST'])
def create_account_page():
    if request.method == 'POST':
        f = request.form

        passport_val = f.get('passport_number')
        if passport_val:
            passport_val = passport_val.upper()
        success, message = Customer.register(
            f.get('email'), f.get('first_name'), f.get('last_name'),
            f.get('date_of_birth'), passport_val,
            f.get('password'), request.form.getlist('phone_numbers')
        )

        if success:
            session['user_id'] = f.get('email')
            session['first_name'] = f.get('first_name')
            session['role'] = 'customer'
            session['email'] = f.get('email')
            flash(f"Registration successful! Welcome to FlyTAU, {f.get('first_name')}.", "success")
            return redirect(url_for('home_page'))

        return render_template('create_account.html', error=message, **f)
    return render_template('create_account.html')

"""Authenticates managers and initializes administrative sessions with elevated access rights"""
@app.route('/manager-login', methods=['GET', 'POST'])
def manager_login_page():
    if request.method == 'POST':
        manager = Manager.login(request.form.get('id_worker'), request.form.get('password'))
        if manager:
            session['user_id'] = manager.id_worker
            session['first_name'] = manager.first_name
            session['role'] = 'manager'
            return redirect(url_for('manager_dashboard'))
        flash("Access Denied: Invalid Credentials", "danger")
    return render_template('manager_login.html')

"""Terminates the current user session and clears all stored authentication data before redirecting to the landing page"""
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home_page'))

# --- Section 3: Customer Actions ---

"""Retrieves and displays categorized booking history for both registered members and guest users"""
@app.route('/my-bookings', methods=['GET', 'POST'])
def view_bookings():
    now = datetime.now()

    if request.method == 'POST':
        email = request.form.get('email')
        booking_id = request.form.get('id_booking')

        if not email or not booking_id:
            flash("Please provide both email and booking ID.", "error")
            return render_template('search_bookings.html')

        single_booking = Booking.get_specific_booking(email, booking_id)

        if single_booking:
            conf, comp, c_you, c_sys = Booking.organize_bookings([single_booking])

            return render_template('booking_results.html',
                                   confirmed=conf, completed=comp,
                                   cancelled_by_you=c_you, cancelled_by_system=c_sys,
                                   is_guest=True, now=now)
        else:
            flash("No booking found with these details.", "error")
            return render_template('search_bookings.html')

    user_email = session.get('email')
    if user_email:
        conf, comp, c_you, c_sys = Booking.get_user_bookings(user_email)
        return render_template('booking_results.html',
                               confirmed=conf, completed=comp,
                               cancelled_by_you=c_you, cancelled_by_system=c_sys,
                               is_guest=False, now=now)

    return render_template('search_bookings.html')

"""Handles the cancellation of an existing booking and provides a status update to the customer"""
@app.route("/cancel-booking", methods=["POST"])
def cancel_booking():
    booking_id = request.form.get('id_booking')

    if not booking_id:
        flash("Invalid request.", "error")
        return redirect(url_for('view_bookings'))

    success, message = Booking.cancel_by_customer(booking_id)
    flash(message, "success" if success else "error")

    return redirect(url_for('view_bookings'))

# --- Section 4: Management ---

"""Displays the administrative dashboard with real-time flight and route data for authorized managers"""
@app.route('/manager/dashboard')
def manager_dashboard():
    if session.get('role') != 'manager':
        return redirect(url_for('manager_login_page'))

    flights, routes = Manager.get_dashboard_data()

    return render_template('manager_dashboard.html', flights=flights, form_data={'routes': routes})

"""Provides a secure API endpoint for real-time validation of aircraft and crew availability, ensuring operational feasibility before a flight is scheduled"""
@app.route("/api/check_availability", methods=['POST'])
def check_availability_api():
    if session.get("role") != "manager":
        return jsonify({"can_proceed": False, "error_msg": "Unauthorized"}), 403

    data = request.get_json()
    if not data or not data.get('route_id') or not data.get('dept_time'):
        return jsonify({"can_proceed": False, "error_msg": "Missing data"}), 400

    response = Manager.validate_resources(data['dept_time'], data['route_id'])

    if not response:
        return jsonify({"can_proceed": False, "error_msg": "Resource calculation error"}), 200
    return jsonify(response)

@app.route("/manager/add_flight", methods=['POST'])
def add_flight():
    if session.get("role") != "manager":
        return redirect(url_for('manager_login_page'))

    manager_id = session.get('user_id')

    route_id = request.form.get('id_route')
    plane_id = request.form.get('id_plane')
    dept_time = request.form.get('departure_time')
    pilots = request.form.getlist('pilots')
    attendants = request.form.getlist('attendants')

    price_economy = request.form.get('price_economy')
    price_business = request.form.get('price_business')
    success, msg = Manager.create_flight(
        route_id, plane_id, dept_time, pilots, attendants, manager_id,
        price_economy, price_business
    )

    if success:
        flash("Flight created successfully!", "success")
    else:
        flash(f"Error creating flight: {msg}", "error")

    return redirect(url_for('manager_dashboard'))

"""Schedules a new flight by committing assigned resources and pricing data to the database"""
@app.route("/manager/cancel_flight", methods=["POST"])
def manager_cancel_flight_route():
    if session.get("role") != "manager":
        flash("Unauthorized access.", "error")
        return redirect(url_for("manager_login_page"))

    flight_id = request.form.get("flight_id")
    if not flight_id:
        flash("Missing flight ID.", "error")
        return redirect(url_for("manager_dashboard"))

    success, message = Manager.cancel_flight(flight_id)

    flash(message, "success" if success else "error")
    return redirect(url_for("manager_dashboard"))

"""Processes comprehensive flight cancellations and updates all related booking statuses in the system"""
@app.route('/manager/manage-aircraft')
def manage_aircraft():
    if session.get('role') != 'manager':
        return redirect(url_for('manager_login_page'))
    resources = Manager.get_all_resources()
    edit_type = request.args.get('edit_type')
    edit_id = request.args.get('edit_id')
    item_to_edit = None
    if edit_id and edit_type:
        item_to_edit = Manager.get_single_resource(edit_type, edit_id)
    add_type = request.args.get('add_type')

    return render_template('manage_aircraft.html',
                           pilots=resources['pilots'],
                           attendants=resources['attendants'],
                           planes=resources['planes'],
                           item_to_edit=item_to_edit,
                           edit_type=edit_type,
                           add_type=add_type)

"""Processes form data to add or update pilots, attendants, and aircraft records in the system"""
@app.route('/manager/save_resource', methods=['POST'])
def save_resource():
    if 'user_id' not in session or session.get('role') != 'manager':
        return redirect('/')

    resource_type = request.form.get('resource_type')
    mode = request.form.get('mode')

    print(f"--- Action: {mode} {resource_type} ---")

    success = False
    if mode == 'add':
        success = Manager.add_new_resource(resource_type, request.form)
    elif mode == 'edit':
        success = Manager.update_existing_resource(resource_type, request.form)

    if success:
        flash(f"{resource_type.capitalize()} saved successfully!", "success")
    else:
        flash("Error saving resource. Check ID or duplicates.", "error")
    return redirect(url_for('manage_aircraft'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
