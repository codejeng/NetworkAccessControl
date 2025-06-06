from flask import Flask, request, jsonify, render_template
import sqlite3
import json
from datetime import datetime, timedelta
import os

app = Flask(__name__)

# Database configuration
DATABASE = 'room_access.db'
#commit to push

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
def check_access():
    """Check if RFID card has access to room at current time"""
    try:
        data = request.get_json()
        rfid_uid = data.get('rfid_uid', '').upper()
        device_mac = data.get('device_mac', '')
        room = data.get('room', '')
        
        conn = get_db_connection()
        
        # Get current time
        current_time = datetime.now()
        
        # Check if user has an active request for this room
        active_request = conn.execute('''
            SELECT r.*, u.name as user_name 
            FROM request_table r
            JOIN user_table u ON r.uid = u.rfid_uid
            WHERE r.uid = ? AND r.room = ? AND r.access = TRUE
            AND r.start_time <= ? AND r.end_time >= ?
            AND r.status = 'approved'
            ORDER BY r.start_time DESC
            LIMIT 1
        ''', (rfid_uid, room, current_time, current_time)).fetchone()
        
        if active_request:
            # Log successful access
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, room, access_granted, device_mac, device_ip, request_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (rfid_uid, room, True, device_mac, request.remote_addr, active_request['id'], 'Valid room request'))
            conn.commit()
            
            response = {
                'access_granted': True,
                'user_name': active_request['user_name'],
                'request_id': active_request['id'],
                'end_time': active_request['end_time'],
                'message': f"Access granted for {active_request['user_name']} until {active_request['end_time']}"
            }
        else:
            # Check if user exists
            user = conn.execute('SELECT * FROM user_table WHERE rfid_uid = ?', (rfid_uid,)).fetchone()
            
            # Log failed access attempt
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, room, access_granted, device_mac, device_ip, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (rfid_uid, room, False, device_mac, request.remote_addr, 
                  'No active room request' if user else 'Unknown card'))
            conn.commit()
            
            response = {
                'access_granted': False,
                'user_name': user['name'] if user else None,
                'request_id': None,
                'message': 'No active room request found' if user else 'Unknown RFID card'
            }
        
        conn.close()
        return jsonify(response)
        
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
        uid = data.get('uid', '').upper().strip()
        start_time = data.get('start_time', '').strip()
        end_time = data.get('end_time', '').strip()
        room = data.get('room', '').strip()
        
        if not all([uid, start_time, end_time, room]):
            return jsonify({'error': 'All fields are required', 'success': False}), 400
        
        conn = get_db_connection()
        
        # Check if user exists
        user = conn.execute('SELECT * FROM users WHERE rfid_uid = ?', (uid,)).fetchone()
        if not user:
            conn.close()
            return jsonify({'error': 'User not found', 'success': False}), 404
        
        # Check if room exists
        room_check = conn.execute('SELECT * FROM rooms WHERE room = ?', (room,)).fetchone()
        if not room_check:
            conn.close()
            return jsonify({'error': 'Room not found', 'success': False}), 404
        
        # Validate time format and logic
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            
            if start_dt >= end_dt:
                conn.close()
                return jsonify({'error': 'End time must be after start time', 'success': False}), 400
            
            if start_dt < datetime.now():
                conn.close()
                return jsonify({'error': 'Start time cannot be in the past', 'success': False}), 400
                
        except ValueError:
            conn.close()
            return jsonify({'error': 'Invalid time format', 'success': False}), 400
        
        # Check for overlapping requests
        overlapping = conn.execute('''
            SELECT * FROM requests 
            WHERE room = ? AND access = TRUE
            AND (
                (datetime(start_time) <= datetime(?) AND datetime(end_time) > datetime(?))
                OR (datetime(start_time) < datetime(?) AND datetime(end_time) >= datetime(?))
                OR (datetime(start_time) >= datetime(?) AND datetime(end_time) <= datetime(?))
            )
        ''', (room, start_time, start_time, end_time, end_time, start_time, end_time)).fetchone()
        
        if overlapping:
            conn.close()
            return jsonify({'error': 'Room is already booked for this time period', 'success': False}), 400
        
        # Insert request
        conn.execute('''
            INSERT INTO requests (uid, name, start_time, end_time, room)
            VALUES (?, ?, ?, ?, ?)
        ''', (uid, user['name'], start_time, end_time, room))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Request submitted successfully',
            'request_details': {
                'uid': uid,
                'name': user['name'],
                'start_time': start_time,
                'end_time': end_time,
                'room': room
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/student/my_requests/<uid>', methods=['GET'])
def get_student_requests(uid):
    """Get all requests for a specific student"""
    try:
        uid = uid.upper().strip()
        
        conn = get_db_connection()
        requests = conn.execute('''
            SELECT r.*, u.name FROM requests r
            JOIN users u ON r.uid = u.rfid_uid
            WHERE r.uid = ?
            ORDER BY r.timestamp DESC
        ''', (uid,)).fetchall()
        
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