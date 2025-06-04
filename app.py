from flask import Flask, request, jsonify, render_template_string
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
    dashboard_html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>RFID Access Control Dashboard</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            h1, h2 {
                color: #333;
                border-bottom: 2px solid #007bff;
                padding-bottom: 10px;
            }
            .stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin: 20px 0;
            }
            .stat-card {
                background: #007bff;
                color: white;
                padding: 20px;
                border-radius: 8px;
                text-align: center;
            }
            .stat-card h3 {
                margin: 0 0 10px 0;
                font-size: 2em;
            }
            .stat-card p {
                margin: 0;
                opacity: 0.9;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
            }
            th, td {
                border: 1px solid #ddd;
                padding: 12px;
                text-align: left;
            }
            th {
                background-color: #f8f9fa;
                font-weight: bold;
            }
            tr:nth-child(even) {
                background-color: #f8f9fa;
            }
            .access-granted {
                color: #28a745;
                font-weight: bold;
            }
            .access-denied {
                color: #dc3545;
                font-weight: bold;
            }
            .btn {
                background: #007bff;
                color: white;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
            }
            .btn:hover {
                background: #0056b3;
            }
            .btn-danger {
                background: #dc3545;
            }
            .btn-danger:hover {
                background: #c82333;
            }
            .form-group {
                margin: 10px 0;
            }
            .form-group label {
                display: block;
                margin-bottom: 5px;
                font-weight: bold;
            }
            .form-group input, .form-group select {
                width: 100%;
                padding: 8px;
                border: 1px solid #ddd;
                border-radius: 4px;
                box-sizing: border-box;
            }
            .section {
                margin: 30px 0;
                padding: 20px;
                border: 1px solid #ddd;
                border-radius: 8px;
            }
            .refresh-btn {
                float: right;
                margin-top: -10px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚪 RFID Access Control Dashboard</h1>
            
            <div class="stats" id="stats">
                <!-- Stats will be loaded here -->
            </div>
            
            <div class="section">
                <h2>👥 Registered Users 
                    <a href="#" class="btn refresh-btn" onclick="loadUsers()">🔄 Refresh</a>
                </h2>
                <div style="margin: 20px 0;">
                    <button class="btn" onclick="showAddUserForm()">➕ Add New User</button>
                </div>
                
                <div id="addUserForm" style="display: none; background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3>Add New User</h3>
                    <div class="form-group">
                        <label>Name:</label>
                        <input type="text" id="userName" placeholder="Enter user name">
                    </div>
                    <div class="form-group">
                        <label>RFID UID:</label>
                        <input type="text" id="userRfid" placeholder="Enter RFID UID (e.g., AB123456)">
                    </div>
                    <div class="form-group">
                        <label>Access Level:</label>
                        <select id="userAccess">
                            <option value="true">Grant Access</option>
                            <option value="false">Deny Access</option>
                        </select>
                    </div>
                    <button class="btn" onclick="addUser()">Add User</button>
                    <button class="btn btn-danger" onclick="hideAddUserForm()">Cancel</button>
                </div>
                
                <table id="usersTable">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Name</th>
                            <th>RFID UID</th>
                            <th>Access</th>
                            <th>Created</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="usersBody">
                        <!-- Users will be loaded here -->
                    </tbody>
                </table>
            </div>
            
            <div class="section">
                <h2>📊 Recent Access Logs
                    <a href="#" class="btn refresh-btn" onclick="loadLogs()">🔄 Refresh</a>
                </h2>
                <table id="logsTable">
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>RFID UID</th>
                            <th>User</th>
                            <th>Result</th>
                            <th>Device IP</th>
                        </tr>
                    </thead>
                    <tbody id="logsBody">
                        <!-- Logs will be loaded here -->
                    </tbody>
                </table>
            </div>
        </div>
        
        <script>
            // Load dashboard data on page load
            window.onload = function() {
                loadStats();
                loadUsers();
                loadLogs();
                
                // Auto-refresh every 30 seconds
                setInterval(function() {
                    loadStats();
                    loadLogs();
                }, 30000);
            };
            
            function loadStats() {
                fetch('/api/stats')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('stats').innerHTML = `
                            <div class="stat-card">
                                <h3>${data.total_users}</h3>
                                <p>Total Users</p>
                            </div>
                            <div class="stat-card">
                                <h3>${data.active_users}</h3>
                                <p>Active Users</p>
                            </div>
                            <div class="stat-card">
                                <h3>${data.today_accesses}</h3>
                                <p>Today's Accesses</p>
                            </div>
                            <div class="stat-card">
                                <h3>${data.success_rate}%</h3>
                                <p>Success Rate</p>
                            </div>
                        `;
                    });
            }
            
            function loadUsers() {
                fetch('/api/users')
                    .then(response => response.json())
                    .then(data => {
                        const tbody = document.getElementById('usersBody');
                        tbody.innerHTML = '';
                        
                        data.users.forEach(user => {
                            const row = `
                                <tr>
                                    <td>${user.id}</td>
                                    <td>${user.name}</td>
                                    <td>${user.rfid_uid}</td>
                                    <td class="${user.has_access ? 'access-granted' : 'access-denied'}">
                                        ${user.has_access ? '✅ Granted' : '❌ Denied'}
                                    </td>
                                    <td>${new Date(user.created_at).toLocaleString()}</td>
                                    <td>
                                        <button class="btn ${user.has_access ? 'btn-danger' : ''}" 
                                                onclick="toggleAccess(${user.id}, ${!user.has_access})">
                                            ${user.has_access ? 'Revoke' : 'Grant'}
                                        </button>
                                        <button class="btn btn-danger" onclick="deleteUser(${user.id})">Delete</button>
                                    </td>
                                </tr>
                            `;
                            tbody.innerHTML += row;
                        });
                    });
            }
            
            function loadLogs() {
                fetch('/api/access_logs')
                    .then(response => response.json())
                    .then(data => {
                        const tbody = document.getElementById('logsBody');
                        tbody.innerHTML = '';
                        
                        data.logs.forEach(log => {
                            const row = `
                                <tr>
                                    <td>${new Date(log.timestamp).toLocaleString()}</td>
                                    <td>${log.rfid_uid}</td>
                                    <td>${log.user_name || 'Unknown'}</td>
                                    <td class="${log.access_granted ? 'access-granted' : 'access-denied'}">
                                        ${log.access_granted ? '✅ Granted' : '❌ Denied'}
                                    </td>
                                    <td>${log.device_ip || 'N/A'}</td>
                                </tr>
                            `;
                            tbody.innerHTML += row;
                        });
                    });
            }
            
            function showAddUserForm() {
                document.getElementById('addUserForm').style.display = 'block';
            }
            
            function hideAddUserForm() {
                document.getElementById('addUserForm').style.display = 'none';
                document.getElementById('userName').value = '';
                document.getElementById('userRfid').value = '';
            }
            
            function addUser() {
                const name = document.getElementById('userName').value;
                const rfid = document.getElementById('userRfid').value.toUpperCase();
                const access = document.getElementById('userAccess').value === 'true';
                
                if (!name || !rfid) {
                    alert('Please fill in all fields');
                    return;
                }
                
                fetch('/api/users', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({name: name, rfid_uid: rfid, has_access: access})
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        hideAddUserForm();
                        loadUsers();
                        loadStats();
                        alert('User added successfully!');
                    } else {
                        alert('Error: ' + data.message);
                    }
                });
            }
            
            function toggleAccess(userId, newAccess) {
                fetch(`/api/users/${userId}/access`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({has_access: newAccess})
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        loadUsers();
                        loadStats();
                    } else {
                        alert('Error updating access');
                    }
                });
            }
            
            function deleteUser(userId) {
                if (confirm('Are you sure you want to delete this user?')) {
                    fetch(`/api/users/${userId}`, {method: 'DELETE'})
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            loadUsers();
                            loadStats();
                        } else {
                            alert('Error deleting user');
                        }
                    });
                }
            }
        </script>
    </body>
    </html>
    '''
    return dashboard_html

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