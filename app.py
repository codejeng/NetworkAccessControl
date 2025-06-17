from flask import Flask, request, jsonify, render_template, session, redirect, url_for, flash
from functools import wraps
import hashlib
import sqlite3
from datetime import datetime, timedelta
import logging
import threading
import time
import requests

app = Flask(__name__)

# Database configuration
DATABASE = 'database.db'

app.secret_key = 'NACS'

ADMIN_CREDENTIALS = {
    "admin": "admin123",
}

SESSION_TIMEOUT = timedelta(hours=24)

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
    
    # Users table - stores all which users has registered
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users_reg (
        id INTEGER PRIMARY KEY,
        uuid TEXT NOT NULL,
        user_id TEXT NOT NULL,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'student',
        is_deleted BOOLEAN NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

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
            auto_approve BOOLEAN NOT NULL DEFAULT 0,
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
            expired_status TEXT NOT NULL DEFAULT 'online',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room) REFERENCES rooms(room)
        )
    ''')
    
    # Insert sample data if tables are empty
    cursor.execute('SELECT COUNT(*) FROM users')
    if cursor.fetchone()[0] == 0:
        sample_users = [
            ('EA20B1CC', 'Apichet Thamraksa', 'student'),
            ('631FBF15', 'Testing Name', 'student'),
            ('C3228B12', 'Testing Name2', 'admin'),
        ]
        
        cursor.executemany(
            'INSERT INTO users (rfid_uid, name, role) VALUES (?, ?, ?)',
            sample_users
        )
        print("✅ Sample users added to database")

    cursor.execute('SELECT COUNT(*) FROM users_reg')
    if cursor.fetchone()[0] == 0:
        sample_users = [
            ('EA20B1CC', '6630406711', 'Apichet', 'Thamraksa', 'Apichet Thamraksa','apichet.t@kkumail.com', 'student'),
            ('631FBF15', '6630406712', 'Testing', 'Name', 'Testing Name', 'test@kkumail.com', 'student'),
            ('C3228B12', '6630406713', 'Testing', 'Name2', 'Testing Name2', 'test@kku.ac.th', 'admin'),
        ]
        
        cursor.executemany(
            'INSERT INTO users_reg (uuid, user_id, first_name, last_name, name, email, role) VALUES (?, ?, ?, ?, ?, ?, ?)',
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

# Authentication functions
def login_required(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_user' not in session:
            return redirect(url_for('admin_login'))
        
        # Check session timeout
        if 'login_time' in session:
            login_time = datetime.fromisoformat(session['login_time'])
            if datetime.now() - login_time > SESSION_TIMEOUT:
                session.clear()
                flash('Session expired. Please login again.', 'error')
                return redirect(url_for('admin_login'))
        
        return f(*args, **kwargs)
    return decorated_function

# API authentication decorator (for AJAX requests)
def api_login_required(f):
    """Decorator for API endpoints that require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_user' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Check session timeout
        if 'login_time' in session:
            login_time = datetime.fromisoformat(session['login_time'])
            if datetime.now() - login_time > SESSION_TIMEOUT:
                session.clear()
                return jsonify({'error': 'Session expired'}), 401
        
        return f(*args, **kwargs)
    return decorated_function


def background_tasks(update_interval=10, cleanup_interval=10):
    """Start background threads for updating expired statuses and cleaning up expired cache"""

    def update_expired_status_worker():
        """Thread to update esp32_cache expired_status periodically"""
        while True:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                cache_entries = cursor.execute('''
                                               SELECT ec.id, ec.name, users_reg.role, ec.expires_at
                                               FROM esp32_cache ec
                                               JOIN users_reg ON ec.name = users_reg.name
                                               ''').fetchall()
                current_time = datetime.now()

                for entry in cache_entries:
                    end_time_str = entry['expires_at']
                    try:
                        if 'T' in end_time_str:
                            end_time = datetime.fromisoformat(end_time_str.replace('T', ' '))
                        else:
                            end_time = datetime.strptime(end_time_str, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        end_time = datetime.min

                    expired_status = 'offline' if current_time > end_time else 'online'

                    if entry['role'] != 'admin':
                        cursor.execute('''
                            UPDATE esp32_cache
                            SET expired_status = ?
                            WHERE id = ?
                        ''', (expired_status, entry['id']))

                conn.commit()
                conn.close()
                time.sleep(update_interval)

            except Exception as e:
                logger.error(f"❌ Error in expired status updater: {str(e)}")
                time.sleep(5)

    def cleanup_cache_worker():
        """Thread to remove expired users periodically"""
        while True:
            try:
                print("🔄 Running periodic cache cleanup...")
                cleanup_expired_cache_entries()
                time.sleep(cleanup_interval)
            except Exception as e:
                print(f"❌ Error in cleanup worker: {str(e)}")
                time.sleep(5)

    # Start both threads
    threading.Thread(target=update_expired_status_worker, daemon=True).start()
    threading.Thread(target=cleanup_cache_worker, daemon=True).start()

    print("✅ Background tasks started: expired status updater and cache cleaner")

def get_cache_with_expired_status():
    """Helper function to get cache entries with expired_status field"""
    try:
        conn = get_db_connection()
        
        # Get all cache entries
        cache_entries = conn.execute('''
            SELECT ec.*, r.ip_address, r.status as room_status
            FROM esp32_cache ec
            JOIN rooms r ON ec.room = r.room
            ORDER BY ec.room, ec.expires_at
        ''').fetchall()
        
        cache_list = []
        
        for entry in cache_entries:
            # Get current time as datetime object
            current_time = datetime.now()
            
            # Parse end_time to datetime object (handle different formats)
            end_time_str = entry['expires_at']
            try:
                # Try ISO format first (2025-06-10T14:50:00)
                if 'T' in end_time_str:
                    end_time = datetime.fromisoformat(end_time_str.replace('T', ' '))
                else:
                    # Standard format (2025-06-10 14:50:00)
                    end_time = datetime.strptime(end_time_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                # Fallback: assume it's expired if we can't parse
                end_time = datetime.min
            
            # Set expired_status based on condition
            # if current time > end time -> true {expired_status = 'offline'}
            # -> false {expired_status = 'online'}
            expired_status = 'offline' if current_time > end_time else 'online'
            
            cache_list.append({
                'id': entry['id'],
                'room': entry['room'],
                'rfid_uid': entry['rfid_uid'],
                'name': entry['name'],
                'expires_at': entry['expires_at'],
                'created_at': entry['created_at'],
                'room_ip': entry['ip_address'],
                'room_status': entry['room_status'],
                'expired_status': expired_status  # Default 'online', 'offline' if expired
            })
        
        conn.close()
        return cache_list
        
    except Exception as e:
        logger.error(f"Error in get_cache_with_expired_status: {str(e)}")
        return []

def send_remove_user_to_esp32(room_ip, rfid_uid):
    """Send remove user request to ESP32"""
    try:
        url = f"http://{room_ip}/api/remove_user"
        payload = {
            "rfid_uid": rfid_uid,
            "action": "remove_expired"
        }
        
        response = requests.post(url, json=payload, timeout=5)
        
        print(f"response request status: {response.status_code}")

        if response.status_code == 200:
            print(f"✅ Successfully sent remove request to {room_ip} for UID {rfid_uid}")
            return True
        else:
            print(f"❌ Failed to send remove request to {room_ip}: HTTP {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Error sending remove request to {room_ip}: {str(e)}")
        return False

def cleanup_expired_cache_entries():
    """Clean up expired cache entries and notify ESP32 devices"""
    try:
        conn = get_db_connection()
        
        # Find all expired entries that are still marked as active
        expired_entries = conn.execute('''
            SELECT ec.*, r.ip_address, r.status as room_status, r.id as room_id
            FROM esp32_cache ec
            JOIN rooms r ON ec.room = r.room
            WHERE expired_status = 'offline'
            AND room_status = 'online'
            AND r.ip_address IS NOT NULL
            ORDER BY ec.room, ec.expires_at
        ''').fetchall()
        
        if expired_entries:
            print(f"🧹 Found {len(expired_entries)} expired cache entries to clean up")
            
            for entry in expired_entries:

                cache_id = entry['id']
                rfid_uid = entry['rfid_uid']
                room = entry['room']
                room_ip = entry['ip_address']
                name = entry['name']
                
                print(f"📤 Sending remove request to ESP32 {room} ({room_ip}) for user {name} ({rfid_uid})")
                
                # Send remove request to ESP32
                success = send_remove_user_to_esp32(room_ip, rfid_uid)
                
                if success:
                    # Update the database to mark as cleaned up
                    conn.execute('''DELETE
                                 FROM esp32_cache
                                 WHERE rfid_uid = ?
                                 AND expired_status = 'offline'
                                 ''', (rfid_uid,))
                    
                    print(f"✅ Removed cache entry id:{cache_id}")
                else:
                    print(f"❌ Failed to clean up cache entry id:{rfid_uid} success : {success}")
        
        conn.commit()
        conn.close()
        
        if expired_entries:
            print(f"🧹 Cache cleanup completed: {len(expired_entries)} entries processed")
        
    except Exception as e:
        print(f"❌ Error in cleanup_expired_cache_entries: {str(e)}")

@app.route('/')
def Home():
    """Admin dashboard for managing room access"""
    return render_template('home.html')

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
            SELECT id, uuid, name, role 
            FROM users_reg 
            WHERE uuid = ?
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
                'debug_info': f'RFID {rfid_uid} not found in users_reg table'
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
            JOIN users_reg u ON r.uid = u.uuid
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
            SELECT rfid_uid, name, expires_at 
            FROM esp32_cache
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
        user_id = data.get('user_id', '').strip()
        start_date = data.get('start_date', '').strip()
        start_time = data.get('start_time', '').strip()
        end_date = data.get('end_date', '').strip()
        end_time = data.get('end_time', '').strip()
        room = data.get('room', '').strip()
        
        if not all([user_id, start_date, start_time, end_date, end_time, room]):
            return jsonify({'error': 'All fields are required', 'success': False}), 400
        
        conn = get_db_connection()
        
        # Find user by name
        user = conn.execute('SELECT * FROM users_reg WHERE user_id = ?', (user_id,)).fetchone()
        if not user:
            conn.close()
            return jsonify({'error': 'ไม่พบรหัสประจำตัวของผู้ใช้งานในฐานข้อมูล', 'success': False}), 404
        
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

        # Check auto approve_request room 
        auto_approve = conn.execute('''
            SELECT auto_approve
            FROM rooms
            WHERE room = ?
        ''', (room,)).fetchone()

        if auto_approve is not None:
            print('auto approve:', bool(auto_approve[0]))
        else:
            print('Room not found or auto_approve is NULL')
        
        # Insert request
        if bool(auto_approve[0]):
            conn.execute('''
                INSERT INTO requests (uid, name, start_time, end_time, room, access, approved_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user['uuid'], user['name'], start_time_iso, end_time_iso, room, bool(auto_approve[0]), 'Auto Approved'))
        else:
            conn.execute('''
                INSERT INTO requests (uid, name, start_time, end_time, room)
                VALUES (?, ?, ?, ?, ?)
            ''', (user['uuid'], user['name'], start_time_iso, end_time_iso, room))
        
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

@app.route('/api/student/my_requests/<user_id>', methods=['GET'])
def get_student_requests(user_id):
    """Get all requests for a specific student by user_id"""
    try:
        user_id = user_id.strip()
        
        conn = get_db_connection()
        requests = conn.execute('''
            SELECT r.*, u.name, u.user_id 
            FROM requests r
            JOIN users_reg u ON r.uid = u.uuid
            WHERE u.user_id = ?
            ORDER BY r.timestamp DESC
        ''', (user_id,)).fetchall()
        
        conn.close()
        
        requests_list = []
        for req in requests:
            requests_list.append({
                'id': req['id'],
                'uid': req['uid'],
                'name': req['name'],
                'user_id': req['user_id'],
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
@api_login_required
def get_all_requests():
    """Get all room access requests"""
    try:
        conn = get_db_connection()
        requests = conn.execute('''
            SELECT r.*, u.name, u.role 
            FROM requests r
            JOIN users_reg u ON r.uid = u.uuid
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
@api_login_required
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
@api_login_required
def manage_users():
    """Get all users or add new user"""
    if request.method == 'GET':
        try:
            conn = get_db_connection()
            users = conn.execute('''
                SELECT * FROM users_reg ORDER BY created_at DESC
            ''').fetchall()
            conn.close()
            
            users_list = []
            for user in users:
                users_list.append({
                    'id': user['id'],
                    'rfid_uid': user['uuid'],
                    'user_id': user['user_id'],
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
            uuid = data.get('uuid', '').upper().strip()
            user_id = data.get('user_id', '').upper().strip()
            first_name = data.get('first_name', '').strip()
            last_anme = data.get('last_name', '').strip()
            name = data.get('name', '')
            email = data.get('email', '').strip()
            role = data.get('role', 'student').strip().lower()
            
            if not uuid or not name:
                return jsonify({'error': 'RFID UID and name are required', 'success': False}), 400
            
            if role not in ['student',  'admin']:
                return jsonify({'error': 'Invalid role', 'success': False}), 400
            
            conn = get_db_connection()
            
            # Check if RFID already exists
            existing = conn.execute(
                'SELECT id FROM users_reg WHERE uuid = ?', (uuid,)
            ).fetchone()
            
            if existing:
                conn.close()
                return jsonify({'error': 'RFID UID already exists', 'success': False}), 400
            
            # Insert new user
            conn.execute('''
                INSERT INTO users_reg (uuid, user_id, first_name, last_name, name, email, role)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (uuid, user_id, first_name, last_anme, name, email, role))
            
            conn.commit()
            conn.close()
            
            return jsonify({'success': True, 'message': 'User added successfully'})
            
        except Exception as e:
            return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/admin/rooms', methods=['GET'])
@api_login_required
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
                'auto_approve': room['auto_approve'],
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
@api_login_required
def get_esp32_cache_updated():
    """Get current ESP32 cache status with expired_status field"""
    try:
        conn = get_db_connection()
        
        # Get all cache entries
        cache_entries = conn.execute('''
            SELECT ec.*, u.role, r.ip_address, r.status as room_status 
            FROM esp32_cache ec
            JOIN rooms r ON ec.room = r.room
            JOIN users_reg u ON ec.name = u.name
            ORDER BY ec.room, ec.expires_at
        ''').fetchall()
        
        conn.close()
        
        cache_list = []
        for entry in cache_entries:
            # Get current time as datetime object
            current_time = datetime.now()
            
            # Parse end_time to datetime object (handle different formats)
            end_time_str = entry['expires_at']
            try:
                # Try ISO format first (2025-06-10T14:50:00)
                if 'T' in end_time_str:
                    end_time = datetime.fromisoformat(end_time_str.replace('T', ' '))
                else:
                    # Standard format (2025-06-10 14:50:00)
                    end_time = datetime.strptime(end_time_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                # Fallback: assume it's expired if we can't parse
                end_time = datetime.min
            
            # Set expired_status: 'offline' if expired, 'online' if not expired
            if entry['role'] != 'admin':
                expired_status = 'offline' if current_time > end_time else 'online'
            else:
                expired_status = 'online'
            
            cache_list.append({
                'id': entry['id'],
                'room': entry['room'],
                'rfid_uid': entry['rfid_uid'],
                'name': entry['name'],
                'role': entry['role'],
                'expires_at': entry['expires_at'],
                'created_at': entry['created_at'],
                'room_ip': entry['ip_address'],
                'room_status': entry['room_status'],
                'expired_status': expired_status 
            })
        
        return jsonify({
            'cache_entries': cache_list
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/access_logs', methods=['GET'])
def get_access_logs():
    """Get access logs"""
    try:
        limit = request.args.get('limit', 100, type=int)
        
        conn = get_db_connection()
        logs = conn.execute('''
            SELECT al.*, u.name, u.role 
            FROM access_logs al
            LEFT JOIN users_reg u ON al.rfid_uid = u.uuid
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
@api_login_required
def get_admin_stats():
    """Get dashboard statistics"""
    try:
        conn = get_db_connection()
        
        # Total users by role
        stats = {}
        roles = conn.execute('''
            SELECT role, COUNT(*) as count 
            FROM users_reg 
            GROUP BY role
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
@api_login_required
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
@api_login_required
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

@app.route('/api/admin/esp32_cache_with_status_update', methods=['GET'])
def get_esp32_cache_with_status_update():
    """Get ESP32 cache, update room status for expired entries, and persist expired_status"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Step 1: Get all cache entries with room information
        cache_entries = cursor.execute('''
            SELECT ec.*, u.role, r.ip_address, r.status as room_status, r.id as room_id
            FROM esp32_cache ec
            JOIN rooms r ON ec.room = r.room
            JOIN users_reg u ON ec.name = u.name
            ORDER BY ec.room, ec.expires_at
        ''').fetchall()
        
        cache_list = []
        current_time = datetime.now()

        for entry in cache_entries:
            end_time_str = entry['expires_at']
            try:
                if 'T' in end_time_str:
                    end_time = datetime.fromisoformat(end_time_str.replace('T', ' '))
                else:
                    end_time = datetime.strptime(end_time_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                end_time = datetime.min

            # Determine expired status
            if entry['role'] != 'admin':
                expired_status = 'offline' if current_time > end_time else 'online'

            # Update expired_status in the database if necessary
            if entry['role'] != 'admin':
                cursor.execute('''
                    UPDATE esp32_cache
                    SET expired_status = ?
                    WHERE id = ?
                ''', (expired_status, entry['id']))

            cache_list.append({
                'id': entry['id'],
                'room': entry['room'],
                'rfid_uid': entry['rfid_uid'],
                'name': entry['name'],
                'role': entry['role'],
                'expires_at': entry['expires_at'],
                'created_at': entry['created_at'],
                'room_ip': entry['ip_address'],
                'room_status': entry['room_status'],
                'expired_status': expired_status
            })

        conn.commit()
        conn.close()

        return jsonify({
            'cache_entries': cache_list,
            'message': f'Retrieved and updated {len(cache_list)} cache entries with expiration status'
        })

    except Exception as e:
        logger.error(f"Error in get_esp32_cache_with_status_update: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/cleanup_cache', methods=['POST'])
def manual_cleanup_cache():
    """Manually trigger cache cleanup"""
    try:
        cleanup_expired_cache_entries()
        return jsonify({
            'success': True,
            'message': 'Cache cleanup completed successfully'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Cache cleanup failed: {str(e)}'
        }), 500

# Authentication routes
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page and handler"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if not username or not password:
            flash('Username and password are required', 'error')
            return render_template('login.html')
        
        # Check credentials
        if username in ADMIN_CREDENTIALS and ADMIN_CREDENTIALS[username] == password:
            session['admin_user'] = username
            session['login_time'] = datetime.now().isoformat()
            session.permanent = True
            
            flash('Login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password', 'error')
            return render_template('login.html')
    
    # If already logged in, redirect to dashboard
    if 'admin_user' in session:
        return redirect(url_for('admin_dashboard'))
    
    return render_template('login.html')

@app.route('/admin/logout')
@login_required
def admin_logout():
    """Admin logout"""
    username = session.get('admin_user', 'Unknown')
    session.clear()

    flash(f'You have been logged out successfully, {username}!', 'success')
    return render_template('login.html')

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    """Admin dashboard page"""
    admin_user = session.get('admin_user', 'Admin')
    return render_template('dashboard.html', admin_user=admin_user)

# Redirect root admin route to login
@app.route('/admin')
def admin_root():
    """Redirect /admin to login"""
    return redirect(url_for('admin_login'))


if __name__ == '__main__':
    # Initialize database
    init_database()

    print("🚀 Starting Room Access Control Server...")
    print("📊 Admin Dashboard: http://localhost:5000/admin")
    print("📝 Student Request Page: http://localhost:5000/request")
    print("🔌 API Endpoints: http://localhost:5000/api/")
    print("💾 Database: room_access.db")
    
    background_tasks()

    # Run the Flask application
    app.run(host='0.0.0.0', port=5000, debug=False)