from flask import Flask, request, jsonify, render_template
import sqlite3
from datetime import datetime
import logging

app = Flask(__name__)

# Database configuration
DATABASE = 'room_access.db'

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Users table - stores all users with their roles
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rfid_uid TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('student', 'teacher', 'admin')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Requests table - stores room access requests
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            name TEXT NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP NOT NULL,
            access BOOLEAN DEFAULT FALSE,
            room TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_by TEXT,
            approved_at TIMESTAMP,
            FOREIGN KEY (uid) REFERENCES users(rfid_uid)
        )
    ''')
    
    # Rooms table - stores ESP32 device information
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT UNIQUE NOT NULL,
            mac_address TEXT UNIQUE NOT NULL,
            ip_address TEXT,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'offline',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Access logs table - logs all access attempts
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS access_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rfid_uid TEXT NOT NULL,
            room TEXT NOT NULL,
            access_granted BOOLEAN NOT NULL,
            access_type TEXT NOT NULL CHECK(access_type IN ('local', 'database', 'denied')),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    ''')
    
    # ESP32 local cache table - tracks what's stored on each ESP32
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS esp32_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT NOT NULL,
            rfid_uid TEXT NOT NULL,
            name TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room) REFERENCES rooms(room)
        )
    ''')
    
    # Insert sample data if tables are empty
    cursor.execute('SELECT COUNT(*) FROM users')
    if cursor.fetchone()[0] == 0:
        sample_users = [
            ('EA20B1CC', 'Apichet Thamraksa', 'student'),
            ('AB12CD34', 'Teacher John', 'teacher'),
            ('12345678', 'Admin User', 'admin'),
        ]
        
        cursor.executemany(
            'INSERT INTO users (rfid_uid, name, role) VALUES (?, ?, ?)',
            sample_users
        )
        print("✅ Sample users added to database")
    
    conn.commit()
    conn.close()
    print("✅ Database initialized successfully")

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def dashboard():
    """Admin dashboard for managing room access"""
    return render_template('dashboard.html')

@app.route('/request')
def request_page():
    """Student request page"""
    return render_template('request.html')

# ================== ESP32 API ENDPOINTS ==================

@app.route('/api/esp32/register', methods=['POST'])
def register_esp32():
    """ESP32 registers itself with the server"""
    try:
        data = request.get_json()
        room = data.get('room', '').strip()
        mac_address = data.get('mac_address', '').strip()
        ip_address = data.get('ip_address', request.remote_addr)
        
        if not room or not mac_address:
            return jsonify({'error': 'Room name and MAC address are required', 'success': False}), 400
        
        conn = get_db_connection()
        
        # Register or update ESP32 device
        conn.execute('''
            INSERT OR REPLACE INTO rooms (room, mac_address, ip_address, last_seen, status)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'online')
        ''', (room, mac_address, ip_address))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'ESP32 registered successfully for room {room}',
            'room': room,
            'mac_address': mac_address,
            'ip_address': ip_address
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/esp32/check_access', methods=['POST'])
def check_esp32_access():
    """ESP32 checks if RFID card has access to room"""
    try:
        # Get JSON data from request
        data = request.get_json()
        if not data:
            return jsonify({
                'error': 'No JSON data provided', 
                'access_granted': False,
                'success': False
            }), 400
        
        # Extract and validate input parameters
        rfid_uid = data.get('rfid_uid', '').upper().strip()
        room = data.get('room', '').strip()
        mac_address = data.get('mac_address', '').strip()
        
        # Validate required fields
        if not rfid_uid:
            return jsonify({
                'error': 'RFID UID is required', 
                'access_granted': False,
                'success': False
            }), 400
            
        if not room:
            return jsonify({
                'error': 'Room name is required', 
                'access_granted': False,
                'success': False
            }), 400
        
        print(f"[DEBUG] Checking access for RFID: {rfid_uid}, Room: {room}")
        
        conn = get_db_connection()
        
        # Update room status and last seen timestamp
        if mac_address:
            room_update = conn.execute('''
                UPDATE rooms SET last_seen = CURRENT_TIMESTAMP, status = 'online'
                WHERE room = ? OR mac_address = ?
            ''', (room, mac_address))
            print(f"[DEBUG] Updated room status for {room}, rows affected: {room_update.rowcount}")
        
        # Check if user exists in the database
        user = conn.execute('''
            SELECT id, rfid_uid, name, role FROM users WHERE rfid_uid = ?
        ''', (rfid_uid,)).fetchone()
        
        if not user:
            print(f"[DEBUG] Unknown RFID card: {rfid_uid}")
            # Log the failed access attempt
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, room, access_granted, access_type, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (rfid_uid, room, False, 'denied', 'Unknown RFID card'))
            conn.commit()
            conn.close()
            
            return jsonify({
                'access_granted': False,
                'user_name': None,
                'message': 'Unknown RFID card',
                'cache_user': False,
                'success': True,
                'debug_info': f'RFID {rfid_uid} not found in users table'
            })
        
        print(f"[DEBUG] Found user: {user['name']} ({user['role']})")
        
        # Get current timestamp in the same format as database
        current_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[DEBUG] Current time: {current_timestamp}")
        
        # Check for valid, approved request for this user and room
        # Using proper datetime comparison with explicit format
        valid_request = conn.execute('''
            SELECT r.id, r.uid, r.start_time, r.end_time, r.room, r.access, 
                   r.approved_by, r.approved_at, u.name
            FROM requests r
            JOIN users u ON r.uid = u.rfid_uid
            WHERE r.uid = ? 
            AND r.room = ? 
            AND r.access = 1
            AND datetime(r.start_time) <= datetime(?)
            AND datetime(r.end_time) >= datetime(?)
            ORDER BY r.timestamp DESC
            LIMIT 1
        ''', (rfid_uid, room, current_timestamp, current_timestamp)).fetchone()
        
        if valid_request:
            print(f"[DEBUG] Valid request found: ID {valid_request['id']}, expires at {valid_request['end_time']}")
            
            # Grant access and update/add to ESP32 cache
            conn.execute('''
                INSERT OR REPLACE INTO esp32_cache (room, rfid_uid, name, expires_at)
                VALUES (?, ?, ?, ?)
            ''', (room, rfid_uid, user['name'], valid_request['end_time']))
            
            # Log successful access
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, room, access_granted, access_type, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (rfid_uid, room, True, 'database', 
                  f'Valid request found (ID: {valid_request["id"]}), cached until {valid_request["end_time"]}'))
            
            conn.commit()
            conn.close()
            
            return jsonify({
                'access_granted': True,
                'user_name': user['name'],
                'user_role': user['role'],
                'message': f'Access granted for {user["name"]}',
                'cache_user': True,
                'expires_at': valid_request['end_time'],
                'success': True,
                'request_id': valid_request['id'],
                'debug_info': f'Request valid from {valid_request["start_time"]} to {valid_request["end_time"]}'
            })
        else:
            print(f"[DEBUG] No valid request found for {user['name']} in room {room}")
            
            # Check if there are any requests for this user/room (for debugging)
            debug_requests = conn.execute('''
                SELECT id, start_time, end_time, access, approved_by
                FROM requests 
                WHERE uid = ? AND room = ?
                ORDER BY timestamp DESC
                LIMIT 3
            ''', (rfid_uid, room)).fetchall()
            
            debug_info = f"Found {len(debug_requests)} total requests for this user/room. "
            if debug_requests:
                for req in debug_requests:
                    debug_info += f"ID:{req['id']} ({req['start_time']} to {req['end_time']}, approved:{req['access']}) "
            else:
                debug_info += "No requests found at all."
            
            print(f"[DEBUG] {debug_info}")
            
            # Log denied access
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, room, access_granted, access_type, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (rfid_uid, room, False, 'denied', 'No valid request found'))
            
            conn.commit()
            conn.close()
            
            return jsonify({
                'access_granted': False,
                'user_name': user['name'],
                'user_role': user['role'],
                'message': f'No valid request found for {user["name"]} in room {room}',
                'cache_user': False,
                'success': True,
                'debug_info': debug_info
            })
        
    except sqlite3.Error as db_error:
        print(f"[ERROR] Database error: {str(db_error)}")
        return jsonify({
            'error': f'Database error: {str(db_error)}', 
            'access_granted': False,
            'success': False
        }), 500
        
    except Exception as e:
        print(f"[ERROR] Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': f'Server error: {str(e)}', 
            'access_granted': False,
            'success': False
        }), 500
    """ESP32 checks if RFID card has access to room"""
    try:
        data = request.get_json()
        rfid_uid = data.get('rfid_uid', '').upper().strip()
        room = data.get('room', '').strip()
        mac_address = data.get('mac_address', '').strip()
        
        if not rfid_uid or not room:
            return jsonify({'error': 'RFID UID and room are required', 'access_granted': False}), 400
        
        conn = get_db_connection()
        
        # Update room status
        conn.execute('''
            UPDATE rooms SET last_seen = CURRENT_TIMESTAMP, status = 'online'
            WHERE room = ? OR mac_address = ?
        ''', (room, mac_address))
        
        # Check if there's a valid request for this user and room
        current_time = datetime.now()
        
        # First check if user exists
        user = conn.execute('''
            SELECT * FROM users WHERE rfid_uid = ?
        ''', (rfid_uid,)).fetchone()
        
        if not user:
            # Log access attempt
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, room, access_granted, access_type, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (rfid_uid, room, False, 'denied', 'Unknown user'))
            conn.commit()
            conn.close()
            
            return jsonify({
                'access_granted': False,
                'user_name': None,
                'message': 'Unknown RFID card',
                'cache_user': False
            })
        
        # Check for valid request
        valid_request = conn.execute('''
            SELECT r.*, u.name FROM requests r
            JOIN users u ON r.uid = u.rfid_uid
            WHERE r.uid = ? AND r.room = ? AND r.access = TRUE
            AND datetime(r.start_time) <= datetime('now')
            AND datetime(r.end_time) >= datetime('now')
            ORDER BY r.created_at DESC
            LIMIT 1
        ''', (rfid_uid, room)).fetchone()
        
        if valid_request:
            # Grant access and add to ESP32 cache
            conn.execute('''
                INSERT OR REPLACE INTO esp32_cache (room, rfid_uid, name, expires_at)
                VALUES (?, ?, ?, ?)
            ''', (room, rfid_uid, user['name'], valid_request['end_time']))
            
            # Log successful access
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, room, access_granted, access_type, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (rfid_uid, room, True, 'database', f'Request approved, cached until {valid_request["end_time"]}'))
            
            conn.commit()
            conn.close()
            
            return jsonify({
                'access_granted': True,
                'user_name': user['name'],
                'message': f'Access granted for {user["name"]}',
                'cache_user': True,
                'expires_at': valid_request['end_time']
            })
        else:
            # Log denied access
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, room, access_granted, access_type, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (rfid_uid, room, False, 'denied', 'No valid request found'))
            
            conn.commit()
            conn.close()
            
            return jsonify({
                'access_granted': False,
                'user_name': user['name'],
                'message': f'No valid request found for {user["name"]} in room {room}',
                'cache_user': False
            })
        
    except Exception as e:
        return jsonify({'error': str(e), 'access_granted': False}), 500

@app.route('/api/esp32/cleanup_cache', methods=['POST'])
def cleanup_esp32_cache():
    """ESP32 requests cleanup of expired cache entries"""
    try:
        data = request.get_json()
        room = data.get('room', '').strip()
        
        conn = get_db_connection()
        
        # Remove expired entries
        result = conn.execute('''
            DELETE FROM esp32_cache 
            WHERE room = ? AND datetime(expires_at) < datetime('now')
        ''', (room,))
        
        removed_count = result.rowcount
        
        # Get current valid cache entries
        current_cache = conn.execute('''
            SELECT rfid_uid, name, expires_at FROM esp32_cache
            WHERE room = ? AND datetime(expires_at) >= datetime('now')
            ORDER BY expires_at ASC
        ''', (room,)).fetchall()
        
        cache_list = []
        for entry in current_cache:
            cache_list.append({
                'rfid_uid': entry['rfid_uid'],
                'name': entry['name'],
                'expires_at': entry['expires_at']
            })
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'removed_count': removed_count,
            'current_cache': cache_list,
            'message': f'Removed {removed_count} expired entries'
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

# ================== STUDENT API ENDPOINTS ==================

@app.route('/api/student/submit_request', methods=['POST'])
def submit_request():
    """Student submits room access request"""
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        start_date = data.get('start_date', '').strip()
        start_time = data.get('start_time', '').strip()
        end_date = data.get('end_date', '').strip()
        end_time = data.get('end_time', '').strip()
        room = data.get('room', '').strip()
        
        if not all([name, start_date, start_time, end_date, end_time, room]):
            return jsonify({'error': 'All fields are required', 'success': False}), 400
        
        conn = get_db_connection()
        
        # Find user by name
        user = conn.execute('SELECT * FROM users WHERE name = ?', (name,)).fetchone()
        if not user:
            conn.close()
            return jsonify({'error': 'ไม่พบชื่อผู้ใช้งาน', 'success': False}), 404
        
        # Check if room exists
        room_check = conn.execute('SELECT * FROM rooms WHERE room = ?', (room,)).fetchone()
        if not room_check:
            conn.close()
            return jsonify({'error': 'ไม่พบห้องที่ท่านได้เลือกไว้', 'success': False}), 404
        
        # Parse and validate time format
        try:
            # Combine date and time to create datetime objects
            start_datetime_str = f"{start_date} {start_time}:00"
            end_datetime_str = f"{end_date} {end_time}:00"
            
            start_dt = datetime.strptime(start_datetime_str, '%Y-%m-%d %H:%M:%S')
            end_dt = datetime.strptime(end_datetime_str, '%Y-%m-%d %H:%M:%S')
            
            if start_dt >= end_dt:
                conn.close()
                return jsonify({'error': 'เวลาจองสิ้นสุดต้องอยู่ก่อนเวลาจองเริ่มต้น', 'success': False}), 400
            
            # Compare with current time (both are now naive datetime objects)
            current_time = datetime.now()
            if start_dt < current_time:
                conn.close()
                return jsonify({'error': 'เวลาจองเริ่มต้นไม่สามารถเป็นอดีตได้', 'success': False}), 400
                
        except ValueError as ve:
            conn.close()
            return jsonify({'error': f'Invalid time format: {str(ve)}', 'success': False}), 400
        
        # Convert to ISO format for database storage
        start_time_iso = start_dt.isoformat()
        end_time_iso = end_dt.isoformat()
        
        # Check for overlapping requests
        overlapping = conn.execute('''
            SELECT * FROM requests 
            WHERE room = ? AND access = TRUE
            AND (
                (datetime(start_time) <= datetime(?) AND datetime(end_time) > datetime(?))
                OR (datetime(start_time) < datetime(?) AND datetime(end_time) >= datetime(?))
                OR (datetime(start_time) >= datetime(?) AND datetime(end_time) <= datetime(?))
            )
        ''', (room, start_time_iso, start_time_iso, end_time_iso, end_time_iso, start_time_iso, end_time_iso)).fetchone()
        
        if overlapping:
            conn.close()
            return jsonify({'error': 'Room is already booked for this time period', 'success': False}), 400
        
        # Insert request
        conn.execute('''
            INSERT INTO requests (uid, name, start_time, end_time, room)
            VALUES (?, ?, ?, ?, ?)
        ''', (user['rfid_uid'], user['name'], start_time_iso, end_time_iso, room))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Request submitted successfully',
            'request_details': {
                'name': user['name'],
                'start_time': start_time_iso,
                'end_time': end_time_iso,
                'room': room
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/student/my_requests/<name>', methods=['GET'])
def get_student_requests(name):
    """Get all requests for a specific student by name"""
    try:
        name = name.strip()
        
        conn = get_db_connection()
        requests = conn.execute('''
            SELECT r.*, u.name FROM requests r
            JOIN users u ON r.uid = u.rfid_uid
            WHERE u.name = ?
            ORDER BY r.timestamp DESC
        ''', (name,)).fetchall()
        
        conn.close()
        
        requests_list = []
        for req in requests:
            requests_list.append({
                'id': req['id'],
                'uid': req['uid'],
                'name': req['name'],
                'start_time': req['start_time'],
                'end_time': req['end_time'],
                'access': bool(req['access']),
                'room': req['room'],
                'timestamp': req['timestamp'],
                'approved_by': req['approved_by'],
                'approved_at': req['approved_at']
            })
        
        return jsonify({'requests': requests_list})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ================== ADMIN API ENDPOINTS ==================

@app.route('/api/admin/requests', methods=['GET'])
def get_all_requests():
    """Get all room access requests"""
    try:
        conn = get_db_connection()
        requests = conn.execute('''
            SELECT r.*, u.name, u.role FROM requests r
            JOIN users u ON r.uid = u.rfid_uid
            ORDER BY r.timestamp DESC
        ''').fetchall()
        
        conn.close()
        
        requests_list = []
        for req in requests:
            requests_list.append({
                'id': req['id'],
                'uid': req['uid'],
                'name': req['name'],
                'role': req['role'],
                'start_time': req['start_time'],
                'end_time': req['end_time'],
                'access': bool(req['access']),
                'room': req['room'],
                'timestamp': req['timestamp'],
                'approved_by': req['approved_by'],
                'approved_at': req['approved_at']
            })
        
        return jsonify({'requests': requests_list})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/requests/<int:request_id>/approve', methods=['PUT'])
def approve_request(request_id):
    """Approve or deny a room access request"""
    try:
        data = request.get_json()
        approve = data.get('approve', False)
        admin_name = data.get('admin_name', 'Admin')
        
        conn = get_db_connection()
        
        if approve:
            conn.execute('''
                UPDATE requests 
                SET access = TRUE, approved_by = ?, approved_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (admin_name, request_id))
            message = 'Request approved successfully'
        else:
            conn.execute('''
                UPDATE requests 
                SET access = FALSE, approved_by = ?, approved_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (admin_name, request_id))
            message = 'Request denied successfully'
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': message})
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/admin/users', methods=['GET', 'POST'])
def manage_users():
    """Get all users or add new user"""
    if request.method == 'GET':
        try:
            conn = get_db_connection()
            users = conn.execute('''
                SELECT * FROM users ORDER BY created_at DESC
            ''').fetchall()
            conn.close()
            
            users_list = []
            for user in users:
                users_list.append({
                    'id': user['id'],
                    'rfid_uid': user['rfid_uid'],
                    'name': user['name'],
                    'role': user['role'],
                    'created_at': user['created_at']
                })
            
            return jsonify({'users': users_list})
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            rfid_uid = data.get('rfid_uid', '').upper().strip()
            name = data.get('name', '').strip()
            role = data.get('role', 'student').strip().lower()
            
            if not rfid_uid or not name:
                return jsonify({'error': 'RFID UID and name are required', 'success': False}), 400
            
            if role not in ['student', 'teacher', 'admin']:
                return jsonify({'error': 'Invalid role', 'success': False}), 400
            
            conn = get_db_connection()
            
            # Check if RFID already exists
            existing = conn.execute(
                'SELECT id FROM users WHERE rfid_uid = ?', (rfid_uid,)
            ).fetchone()
            
            if existing:
                conn.close()
                return jsonify({'error': 'RFID UID already exists', 'success': False}), 400
            
            # Insert new user
            conn.execute('''
                INSERT INTO users (rfid_uid, name, role)
                VALUES (?, ?, ?)
            ''', (rfid_uid, name, role))
            
            conn.commit()
            conn.close()
            
            return jsonify({'success': True, 'message': 'User added successfully'})
            
        except Exception as e:
            return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/admin/rooms', methods=['GET'])
def get_rooms():
    """Get all registered rooms"""
    try:
        conn = get_db_connection()
        rooms = conn.execute('''
            SELECT * FROM rooms ORDER BY created_at DESC
        ''').fetchall()
        conn.close()
        
        rooms_list = []
        for room in rooms:
            rooms_list.append({
                'id': room['id'],
                'room': room['room'],
                'mac_address': room['mac_address'],
                'ip_address': room['ip_address'],
                'last_seen': room['last_seen'],
                'status': room['status'],
                'created_at': room['created_at']
            })
        
        return jsonify({'rooms': rooms_list})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/esp32_cache', methods=['GET'])
def get_esp32_cache():
    """Get current ESP32 cache status"""
    try:
        conn = get_db_connection()
        cache_entries = conn.execute('''
            SELECT ec.*, r.ip_address, r.status as room_status 
            FROM esp32_cache ec
            JOIN rooms r ON ec.room = r.room
            WHERE datetime(ec.expires_at) >= datetime('now')
            ORDER BY ec.room, ec.expires_at
        ''').fetchall()
        conn.close()
        
        cache_list = []
        for entry in cache_entries:
            cache_list.append({
                'id': entry['id'],
                'room': entry['room'],
                'rfid_uid': entry['rfid_uid'],
                'name': entry['name'],
                'expires_at': entry['expires_at'],
                'created_at': entry['created_at'],
                'room_ip': entry['ip_address'],
                'room_status': entry['room_status']
            })
        
        return jsonify({'cache_entries': cache_list})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/access_logs', methods=['GET'])
def get_access_logs():
    """Get access logs"""
    try:
        limit = request.args.get('limit', 100, type=int)
        
        conn = get_db_connection()
        logs = conn.execute('''
            SELECT al.*, u.name, u.role FROM access_logs al
            LEFT JOIN users u ON al.rfid_uid = u.rfid_uid
            ORDER BY al.timestamp DESC
            LIMIT ?
        ''', (limit,)).fetchall()
        conn.close()
        
        logs_list = []
        for log in logs:
            logs_list.append({
                'id': log['id'],
                'rfid_uid': log['rfid_uid'],
                'room': log['room'],
                'access_granted': bool(log['access_granted']),
                'access_type': log['access_type'],
                'timestamp': log['timestamp'],
                'notes': log['notes'],
                'user_name': log['name'],
                'user_role': log['role']
            })
        
        return jsonify({'logs': logs_list})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/stats', methods=['GET'])
def get_admin_stats():
    """Get dashboard statistics"""
    try:
        conn = get_db_connection()
        
        # Total users by role
        stats = {}
        roles = conn.execute('''
            SELECT role, COUNT(*) as count FROM users GROUP BY role
        ''').fetchall()
        
        for role in roles:
            stats[f'total_{role["role"]}s'] = role['count']
        
        # Total requests
        stats['total_requests'] = conn.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
        
        # Approved requests
        stats['approved_requests'] = conn.execute('SELECT COUNT(*) FROM requests WHERE access = TRUE').fetchone()[0]
        
        # Pending requests
        stats['pending_requests'] = conn.execute('SELECT COUNT(*) FROM requests WHERE access = FALSE AND approved_by IS NULL').fetchone()[0]
        
        # Total rooms
        stats['total_rooms'] = conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0]
        
        # Online rooms
        stats['online_rooms'] = conn.execute('SELECT COUNT(*) FROM rooms WHERE status = "online"').fetchone()[0]
        
        # Today's access attempts
        stats['today_access_attempts'] = conn.execute('''
            SELECT COUNT(*) FROM access_logs 
            WHERE date(timestamp) = date('now')
        ''').fetchone()[0]
        
        # Current active cache entries
        stats['active_cache_entries'] = conn.execute('''
            SELECT COUNT(*) FROM esp32_cache 
            WHERE datetime(expires_at) >= datetime('now')
        ''').fetchone()[0]
        
        conn.close()
        
        return jsonify(stats)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/requests/clear', methods=['DELETE'])
def clear_all_requests():
    # Clear all room access requests from the database

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get count of requests before deletion for logging
        cursor.execute("SELECT COUNT(*) FROM requests")
        request_count = cursor.fetchone()[0]

        # Delete all request
        cursor.execute("DELETE FROM requests")

        logger.info(f"Admin cleared {request_count} room access requests at {datetime.now()}")

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Succesfully cleared {request_count} room access requests',
            'cleared_count': request_count
        }), 200
        
    except sqlite3.Error as e:
        logger.error(f"Database error while clearing requests: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Database error {str(e)}'
        }), 500
    except Exception as e:
        logger.error(f"Unexpected error while clearing requests: {str(e)}")
        return jsonify({
            'success': False,
            'erorr': f'Unexpected error: {str(e)}'
        }), 500

@app.route('/api/admin/esp32_cache/clear', methods=['DELETE'])
def clear_esp32_cache():
    """Clear all ESP32 cache entries from the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get count of cache entries before deletion for logging
        cursor.execute("SELECT COUNT(*) FROM esp32_cache")
        cache_count = cursor.fetchone()[0]
        
        # Delete all cache entries
        cursor.execute("DELETE FROM esp32_cache")
        
        # Log the operation
        logger.info(f"Admin cleared {cache_count} ESP32 cache entries at {datetime.now()}")
        
        conn.commit()
        conn.close()
        
        # Optionally, you might want to notify all ESP32 devices to refresh their cache
        # This could be done through a separate notification system or websockets
        
        return jsonify({
            'success': True,
            'message': f'Successfully cleared {cache_count} ESP32 cache entries',
            'cleared_count': cache_count,
            'warning': 'ESP32 devices will need to refresh their cache for access control to work properly'
        }), 200
        
    except sqlite3.Error as e:
        logger.error(f"Database error while clearing ESP32 cache: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Database error: {str(e)}'
        }), 500
        
    except Exception as e:
        logger.error(f"Unexpected error while clearing ESP32 cache: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Unexpected error: {str(e)}'
        }), 500

if __name__ == '__main__':
    # Initialize database
    init_database()
    
    print("🚀 Starting Room Access Control Server...")
    print("📊 Admin Dashboard: http://localhost:5000")
    print("📝 Student Request Page: http://localhost:5000/request")
    print("🔌 API Endpoints: http://localhost:5000/api/")
    print("💾 Database: room_access.db")
    
    # Run the Flask application
    app.run(host='0.0.0.0', port=5000, debug=True)