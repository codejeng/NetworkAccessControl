from flask import Flask, request, jsonify, render_template
import sqlite3
import json
from datetime import datetime, timedelta
import os

app = Flask(__name__)

# Database configuration
DATABASE = 'rfid_access.db'

def init_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rfid_uid TEXT UNIQUE NOT NULL,
            has_access BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Access logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS access_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rfid_uid TEXT NOT NULL,
            access_granted BOOLEAN NOT NULL,
            device_id TEXT,
            device_ip TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    ''')
    
    # Device sync table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS device_sync (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            last_sync TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_count INTEGER DEFAULT 0
        )
    ''')
    
    # Insert sample data if empty
    cursor.execute('SELECT COUNT(*) FROM users')
    if cursor.fetchone()[0] == 0:
        sample_users = [
            ('Apichet Thamraksa', 'EA20B1CC', True),
        ]
        
        cursor.executemany(
            'INSERT INTO users (name, rfid_uid, has_access) VALUES (?, ?, ?)',
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
    """Web dashboard for managing RFID access"""
    return render_template('dashboard.html')

@app.route('/api/check_access', methods=['POST'])
def check_access():
    """Check if RFID card has access"""
    try:
        data = request.get_json()
        rfid_uid = data.get('rfid_uid', '').upper()
        device_id = data.get('device_id', '')
        
        conn = get_db_connection()
        user = conn.execute(
            'SELECT * FROM users WHERE rfid_uid = ?', (rfid_uid,)
        ).fetchone()
        
        if user:
            # Log the access attempt
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, access_granted, device_id, device_ip)
                VALUES (?, ?, ?, ?)
            ''', (rfid_uid, user['has_access'], device_id, request.remote_addr))
            conn.commit()
            
            response = {
                'access_granted': bool(user['has_access']),
                'user_name': user['name'],
                'user_id': user['id'],
                'message': f"Access {'granted' if user['has_access'] else 'denied'} for {user['name']}"
            }
        else:
            # Log unknown card attempt
            conn.execute('''
                INSERT INTO access_logs (rfid_uid, access_granted, device_id, device_ip, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (rfid_uid, False, device_id, request.remote_addr, 'Unknown card'))
            conn.commit()
            
            response = {
                'access_granted': False,
                'user_name': None,
                'user_id': None,
                'message': 'Unknown RFID card'
            }
        
        conn.close()
        return jsonify(response)
        
    except Exception as e:
        return jsonify({'error': str(e), 'access_granted': False}), 500

@app.route('/api/sync_cards', methods=['POST'])
def sync_cards():
    """Sync all cards for device local storage"""
    try:
        data = request.get_json()
        device_id = data.get('device_id', '')
        
        conn = get_db_connection()
        
        # Update device sync record
        conn.execute('''
            INSERT OR REPLACE INTO device_sync (device_id, last_sync, sync_count)
            VALUES (?, CURRENT_TIMESTAMP, 
                    COALESCE((SELECT sync_count FROM device_sync WHERE device_id = ?), 0) + 1)
        ''', (device_id, device_id))
        
        # Get all active users
        users = conn.execute('''
            SELECT rfid_uid, name, has_access, created_at 
            FROM users 
            ORDER BY created_at DESC
        ''').fetchall()
        
        cards = []
        for user in users:
            cards.append({
                'rfid_uid': user['rfid_uid'],
                'user_name': user['name'],
                'has_access': bool(user['has_access']),
                'created_at': user['created_at']
            })
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'cards': cards,
            'sync_time': datetime.now().isoformat(),
            'total_cards': len(cards)
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/log_access', methods=['POST'])
def log_access():
    """Log access attempt from device"""
    try:
        data = request.get_json()
        rfid_uid = data.get('rfid_uid', '').upper()
        access_granted = data.get('access_granted', False)
        device_id = data.get('device_id', '')
        device_ip = data.get('device_ip', request.remote_addr)
        
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO access_logs (rfid_uid, access_granted, device_id, device_ip, notes)
            VALUES (?, ?, ?, ?, ?)
        ''', (rfid_uid, access_granted, device_id, device_ip, 'Logged from device'))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Access logged successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/users', methods=['GET', 'POST'])
def users():
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
                    'name': user['name'],
                    'rfid_uid': user['rfid_uid'],
                    'has_access': bool(user['has_access']),
                    'created_at': user['created_at'],
                    'updated_at': user['updated_at']
                })
            
            return jsonify({'users': users_list})
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            name = data.get('name', '').strip()
            rfid_uid = data.get('rfid_uid', '').upper().strip()
            has_access = data.get('has_access', True)
            
            if not name or not rfid_uid:
                return jsonify({'error': 'Name and RFID UID are required', 'success': False}), 400
            
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
                INSERT INTO users (name, rfid_uid, has_access)
                VALUES (?, ?, ?)
            ''', (name, rfid_uid, has_access))
            
            conn.commit()
            conn.close()
            
            return jsonify({'success': True, 'message': 'User added successfully'})
            
        except Exception as e:
            return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    """Delete a user"""
    try:
        conn = get_db_connection()
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'User deleted successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/users/<int:user_id>/access', methods=['PUT'])
def update_user_access(user_id):
    """Update user access permission"""
    try:
        data = request.get_json()
        has_access = data.get('has_access', True)
        
        conn = get_db_connection()
        conn.execute('''
            UPDATE users 
            SET has_access = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (has_access, user_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Access updated successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/access_logs', methods=['GET'])
def access_logs():
    """Get recent access logs"""
    try:
        limit = request.args.get('limit', 50, type=int)
        
        conn = get_db_connection()
        logs = conn.execute('''
            SELECT al.*, u.name as user_name
            FROM access_logs al
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
                'user_name': log['user_name'],
                'access_granted': bool(log['access_granted']),
                'device_id': log['device_id'],
                'device_ip': log['device_ip'],
                'timestamp': log['timestamp'],
                'notes': log['notes']
            })
        
        return jsonify({'logs': logs_list})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get dashboard statistics"""
    try:
        conn = get_db_connection()
        
        # Total users
        total_users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        
        # Active users (with access)
        active_users = conn.execute(
            'SELECT COUNT(*) FROM users WHERE has_access = 1'
        ).fetchone()[0]
        
        # Today's access attempts
        today_accesses = conn.execute('''
            SELECT COUNT(*) FROM access_logs 
            WHERE date(timestamp) = date('now')
        ''').fetchone()[0]
        
        # Success rate today
        today_success = conn.execute('''
            SELECT COUNT(*) FROM access_logs 
            WHERE date(timestamp) = date('now') AND access_granted = 1
        ''').fetchone()[0]
        
        success_rate = round((today_success / today_accesses * 100) if today_accesses > 0 else 0, 1)
        
        conn.close()
        
        return jsonify({
            'total_users': total_users,
            'active_users': active_users,
            'today_accesses': today_accesses,
            'success_rate': success_rate
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/rfid', methods=['POST'])
def rfid_endpoint():
    """Legacy endpoint for compatibility with existing code"""
    try:
        data = request.get_json()
        rfid_uid = data.get('rfid_uid', '').upper()
        
        # Log the RFID scan
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO access_logs (rfid_uid, access_granted, device_id, device_ip, notes)
            VALUES (?, ?, ?, ?, ?)
        ''', (rfid_uid, False, data.get('device_info', {}).get('mac_address', ''), 
              request.remote_addr, 'Legacy endpoint scan'))
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'received',
            'message': 'RFID data logged successfully',
            'rfid_uid': rfid_uid,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Initialize database
    init_database()
    
    print("🚀 Starting RFID Access Control Server...")
    print("📊 Dashboard: http://localhost:5000")
    print("🔌 API Endpoint: http://localhost:5000/api/")
    print("💾 Database: rfid_access.db")
    
    # Run the Flask application
    app.run(host='0.0.0.0', port=5000, debug=True)