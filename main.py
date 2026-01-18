from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from models import Customer, Manager, Flight, Booking
from database import Database
from datetime import datetime, timedelta
from utils import get_plane_object, map_occupied_seats, validate_seat_selection, _format_price, prepare_flights_for_view

app = Flask(__name__)
app.secret_key = 'flytau_secret_key'
db = Database()

# --- 1. עמודי לקוח וחיפוש ---

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
    # אתחול המשתנה כדי למנוע שגיאות ב-HTML
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


@app.route('/login', methods=['GET', 'POST'])
def register_login_page():
    email = None
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = Customer.login(email, password)
        if user:
            session['user_id'] = user.email
            session['first_name'] = user.first_name
            session['role'] = 'customer'
            session['email'] = user.email
            return redirect(url_for('home_page'))
        flash("Invalid email or password", "danger")
    return render_template('register_login.html', email=email)


@app.route('/register', methods=['GET', 'POST'])
def create_account_page():
    if request.method == 'POST':
        f = request.form

        # שימי לב: אנחנו שולחים את הנתונים למודל (ודאי שה-HTML שלך שולח 'passport_number')
        passport_val = f.get('passport_number')
        if passport_val:
            passport_val = passport_val.upper()
        success, message = Customer.register(
            f.get('email'), f.get('first_name'), f.get('last_name'),
            f.get('date_of_birth'), passport_val,
            f.get('password'), request.form.getlist('phone_numbers')
        )

        if success:
            # --- השינוי הגדול: כניסה אוטומטית (Auto-Login) ---
            # במקום לשלוח להתחברות, אנחנו מכניסים את הפרטים ל-SESSION מיד
            session['user_id'] = f.get('email')
            session['first_name'] = f.get('first_name')
            session['role'] = 'customer'
            session['email'] = f.get('email')

            # הודעה מעוצבת ומזמינה
            flash(f"Registration successful! Welcome to FlyTAU, {f.get('first_name')}.", "success")

            # שליחה ישירות לדף הבית
            return redirect(url_for('home_page'))

        return render_template('create_account.html', error=message, **f)
    return render_template('create_account.html')


@app.route('/my-bookings', methods=['GET', 'POST'])
def view_bookings():
    """הצגת הזמנות ללקוח רשום או לאורח - גרסה מעודכנת"""
    now = datetime.now()

    if request.method == 'POST':
        # --- לוגיקה לאורח (Guest) ---
        email = request.form.get('email')
        booking_id = request.form.get('id_booking')

        if not email or not booking_id:
            flash("Please provide both email and booking ID.", "error")
            return render_template('search_bookings.html')

        single_booking = Booking.get_specific_booking(email, booking_id)

        if single_booking:
            # שימוש בפונקציה המרכזית שיצרנו במודל למיון ההזמנה
            # אנחנו שולחים רשימה עם פריט אחד ([single_booking]) כי הפונקציה מצפה לרשימה
            conf, comp, c_you, c_sys = Booking.organize_bookings([single_booking])

            return render_template('bookings_results.html',
                                   confirmed=conf, completed=comp,
                                   cancelled_by_you=c_you, cancelled_by_system=c_sys,
                                   is_guest=True, now=now)
        else:
            flash("No booking found with these details.", "error")
            return render_template('search_bookings.html')

    # --- לוגיקה למשתמש רשום (Registered User) ---
    user_email = session.get('email')
    if user_email:
        # כאן נשארנו עם הלוגיקה המקורית שעובדת עבור משתמש רשום
        conf, comp, c_you, c_sys = Booking.get_user_bookings(user_email)
        return render_template('bookings_results.html',
                               confirmed=conf, completed=comp,
                               cancelled_by_you=c_you, cancelled_by_system=c_sys,
                               is_guest=False, now=now)

    return render_template('search_bookings.html')


@app.route("/cancel-booking", methods=["POST"])
def cancel_booking():
    """ביטול הזמנה ע"י לקוח - הגרסה החדשה והנקייה"""
    booking_id = request.form.get('id_booking')

    # בדיקת קלט בסיסית
    if not booking_id:
        flash("Invalid request.", "error")
        return redirect(url_for('view_bookings'))

    # הקסם קורה כאן: שורה אחת במקום כל הלוגיקה וה-SQL שהיו פה
    success, message = Booking.cancel_by_customer(booking_id)

    # הצגת ההודעה למשתמש (הודעת ההצלחה או הכישלון מגיעה מהמודל)
    flash(message, "success" if success else "error")

    return redirect(url_for('view_bookings'))


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


@app.route('/manager/dashboard')
def manager_dashboard():
    # בדיקת הרשאות (נשאר אותו דבר)
    if session.get('role') != 'manager':
        return redirect(url_for('manager_login_page'))

    # הקסם החדש: שורה אחת שמביאה את הכל מוכן מהמודל!
    # אין יותר לולאות, אין חישובי זמנים ואין שרשור מחרוזות ב-Main
    flights, routes = Manager.get_dashboard_data()

    return render_template('manager_dashboard.html', flights=flights, form_data={'routes': routes})

@app.route("/api/check_availability", methods=['POST'])
def check_availability_api():
    """API עבור ה-Wizard ליצירת טיסה"""
    if session.get("role") != "manager":
        return jsonify({"can_proceed": False, "error_msg": "Unauthorized"}), 403

    data = request.get_json()
    if not data or not data.get('route_id') or not data.get('dept_time'):
        return jsonify({"can_proceed": False, "error_msg": "Missing data"}), 400

    # הפעלת הלוגיקה דרך המודל
    response = Manager.validate_resources(data['dept_time'], data['route_id'])

    if not response:
        return jsonify({"can_proceed": False, "error_msg": "שגיאה בחישוב משאבים"}), 200

    return jsonify(response)


@app.route("/manager/cancel_flight", methods=["POST"])
def manager_cancel_flight_route():
    """ביטול טיסה ע"י מנהל"""
    if session.get("role") != "manager":
        flash("Unauthorized access.", "error")
        return redirect(url_for("manager_login_page"))

    flight_id = request.form.get("flight_id")
    if not flight_id:
        flash("Missing flight ID.", "error")
        return redirect(url_for("manager_dashboard"))

    # הפעלת הלוגיקה ב-Model (שקוראת ל-manager_cancel_flight_full_logic ב-DB)
    success, message = Manager.cancel_flight(flight_id)

    flash(message, "success" if success else "error")
    return redirect(url_for("manager_dashboard"))


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

    # --- השינוי: קליטת המחירים מהטופס ---
    price_economy = request.form.get('price_economy')
    price_business = request.form.get('price_business')  # יכול להיות ריק אם המטוס קטן

    # העברת המחירים למודל
    success, msg = Manager.create_flight(
        route_id, plane_id, dept_time, pilots, attendants, manager_id,
        price_economy, price_business
    )

    if success:
        flash("Flight created successfully!", "success")
    else:
        flash(f"Error creating flight: {msg}", "error")

    return redirect(url_for('manager_dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home_page'))

##קוד של אופיר
@app.route('/manager/manage-aircraft') # שיניתי את ה-URL ל-Aircraft
def manage_aircraft(): # שיניתי את שם הפונקציה כדי שיתאים ל-url_for ב-HTML
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

# --- הוספה לניהול משאבים (טייסים, דיילים, מטוסים) ---

@app.route('/manager/save_resource', methods=['POST'])
def save_resource():
    # 1. בדיקת הרשאות מנהל
    if 'user_id' not in session or session.get('role') != 'manager':
        return redirect('/')

    # 2. קבלת הנתונים מהטופס
    resource_type = request.form.get('resource_type') # pilot / aircraft / attendant
    mode = request.form.get('mode') # add / edit

    print(f"--- Action: {mode} {resource_type} ---") # הדפסה ללוג לבדיקה

    # 3. שליחה למודל (Manager -> Database)
    success = False
    if mode == 'add':
        success = Manager.add_new_resource(resource_type, request.form)
    elif mode == 'edit':
        success = Manager.update_existing_resource(resource_type, request.form)

    # 4. הודעה למשתמש וחזרה לטבלה
    if success:
        flash(f"{resource_type.capitalize()} saved successfully!", "success")
    else:
        flash("Error saving resource. Check ID or duplicates.", "error")

    return redirect(url_for('manage_aircraft'))

#קוד של ניבי
@app.route("/select-seats", methods=["GET"])
def select_seats_page():
    flight_id = request.args.get("flight_id")
    if not flight_id:
        return redirect(url_for('home_page'))

    # 1. שליפת נתוני הטיסה
    raw_flight = db.get_flight_data(flight_id=flight_id)
    if not raw_flight:
        return redirect(url_for('home_page'))

    # flight_view יכיל את השדות המעוצבים: departure_display, arrival_display
    flight_view = prepare_flights_for_view(raw_flight)[0]

    # 2. בניית המטוס
    plane = get_plane_object(flight_id)
    if not plane:
        flash("Plane configuration missing.", "error")
        return redirect(url_for('home_page'))

    # 3. מושבים תפוסים ומחירים
    occupied_map = map_occupied_seats(db.get_occupied_seats(flight_id))

    seats_prices = db.get_flight_prices(flight_id)
    # המרה לדולרים: {'Economy': '$100.00', ...}
    formatted_prices = {k: _format_price(v) for k, v in seats_prices.items()}

    return render_template("select_seats.html",
                           flight=flight_view,
                           plane=plane,
                           occupied=occupied_map,
                           prices=formatted_prices,
                           col_letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ")


@app.route("/process-booking", methods=["POST"])
def process_booking():
    flight_id = request.form.get("flight_id")
    selected_seats = request.form.getlist("seats")  # מקבל רשימה של מושבים

    # אם לא נבחרו מושבים
    if not selected_seats:
        flash("Please select at least one seat.", "error")
        return redirect(url_for('select_seats_page', flight_id=flight_id))

    # בדיקה חוזרת ששום דבר לא נתפס בזמן שהמשתמש חשב (משתמש בפונקציה שלך מ-utils)
    conflicts = validate_seat_selection(selected_seats, flight_id)

    if conflicts:
        conflict_msg = ", ".join(conflicts)
        flash(f"Oops! The following seats were just taken: {conflict_msg}. Please choose different seats.")
        return redirect(url_for('select_seats_page', flight_id=flight_id))

    session['current_booking'] = {'flight_id': flight_id, 'seats': selected_seats}
    return redirect(url_for('passenger_details_page'))


@app.route("/passenger-details", methods=["GET"])
def passenger_details_page():
    booking_data = session.get('current_booking')
    if not booking_data:
        return redirect(url_for('home_page'))

    flight_id = booking_data['flight_id']
    seats = booking_data['seats']

    # 1. שליפת פרטי טיסה
    raw_flight = db.get_flight_data(flight_id=flight_id)
    if not raw_flight:
        return redirect(url_for('home_page'))
    flight_view = prepare_flights_for_view(raw_flight)[0]

    # 2. שליפת מחירים ו"נרמול" (הופכים הכל לאותיות קטנות ליתר ביטחון)
    prices_raw = db.get_flight_prices(flight_id)
    # המרה למילון חכם: מנקה רווחים והופך לקטן. דוגמה: " Economy " -> "economy"
    prices_normalized = {k.strip().lower(): v for k, v in prices_raw.items()}


    # 3. חישוב מחיר
    total_price = 0
    seats_list = []

    for s in seats:
        # s מגיע מה-HTML, למשל: 'Economy-1-A'
        parts = s.split('-')
        class_from_html = parts[0]

        # מנקים גם את מה שהגיע מה-HTML (אותיות קטנות + בלי רווחים)
        lookup_key = class_from_html.strip().lower()

        # חיפוש במילון המנורמל
        price = prices_normalized.get(lookup_key, 0)

        print(f"--- DEBUG: Seat '{s}' -> Key '{lookup_key}' -> Found Price: {price}")

        total_price += price

        # שומרים ברשימה עם האות המקורית גדולה (בשביל היופי בטבלה)
        display_class = class_from_html.capitalize()
        seats_list.append({'seat_code': f"{parts[1]}{parts[2]}", 'class': display_class, 'price': price})

    # עדכון המחיר ב-SESSION
    booking_data['total_price'] = total_price
    session['current_booking'] = booking_data

    # --- חישוב מספר הטפסים ---
    total_seats = len(seats)
    num_forms = 2 if total_seats > 2 else total_seats

    # 4. מילוי אוטומטי
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



@app.route("/save-passengers", methods=["POST"])
def save_passengers():
    # 1. בדיקה שיש נתונים ב-session
    # אנחנו משתמשים במשתנה זמני כדי לא לעבוד ישירות על ה-session
    current_booking = session.get('current_booking')
    if not current_booking:
        return redirect("/")

    flight_id = current_booking['flight_id']
    seats = current_booking['seats']

    # 2. בדיקת זמינות
    conflicts = validate_seat_selection(seats, flight_id)
    if conflicts:
        conflict_msg = ", ".join(conflicts)
        flash(f"Oops! The following seats were just taken: {conflict_msg}. Please choose different seats.")
        # מחיקה מה-session במקרה של תקלה
        session.pop('current_booking', None)
        return redirect(url_for('select_seats_page', flight_id=flight_id))

    # 3. עיבוד הטופס
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

    # --- התיקון המשוריין ---
    # 1. מעדכנים את המשתנה המקומי (לא ישירות בתוך ה-session)
    current_booking['passengers'] = passengers_info
    current_booking['total_price'] = total_final_price

    # 2. דורסים את הישן עם החדש - זה מכריח את Flask לשמור שינויים!
    session['current_booking'] = current_booking
    # -----------------------

    return redirect(url_for('booking_summery_page'))





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
        # --- התיקון: מציגים את דף האישור הסופי ---
        return redirect(url_for('booking_confirmation_page', booking_id=booking_id, email=user_email))
    else:
        flash("Error processing payment.", "error")
        return redirect(url_for('booking_summery_page'))





# תקן את הפונקציה הזו - היא צריכה להציג את הסיכום לפני התשלום
@app.route("/booking-summery", methods=["GET"])
def booking_summery_page():
    booking_data = session.get('current_booking')
    if not booking_data:
        return redirect("/")

    flight_id = booking_data['flight_id']
    seat_strings = booking_data['seats']
    passengers = booking_data.get('passengers', [])  # <--- שליפת רשימת הנוסעים המוכנה

    # 1. שליפת פרטי הטיסה
    raw_flight = db.get_flight_data(flight_id=flight_id)
    if raw_flight:
        flight = prepare_flights_for_view(raw_flight)[0]
    else:
        return redirect("/")

    # 2. חישוב מחירים ושורות לטבלה
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

    # 3. התיקון: רינדור דף הסיכום (booking_payment.html) עם המשתנים שחישבנו למעלה
    return render_template("booking_payment.html",
                           flight=flight,
                           summary_rows=summary_rows,
                           total_price=formatted_total)



# --- הוספה קריטית: עמוד האישור ---
@app.route("/booking-confirmation/<int:booking_id>")
def booking_confirmation_page(booking_id):
    # שליפת המייל מה-URL כדי להציג אותו למשתמש
    email = request.args.get('email', '')
    return render_template("booking_confirmation.html", booking_id=booking_id, email=email)




if __name__ == '__main__':
    app.run(debug=True, port=5001)
