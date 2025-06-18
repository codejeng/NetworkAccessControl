from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import sqlite3
import os
import csv
from uuid import uuid4

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')
DB_PATH = os.path.abspath(DB_PATH)

PHOTO_DIR = "photos"

latest_uuid = None

def init_db() :
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS users_reg (
                       id INTEGER PRIMARY KEY,
                       uuid TEXT NOT NULL,
                       user_id TEXT NOT NULL,
                       first_name TEXT NOT NULL,
                       last_name TEXT NOT NULL,
                       name TEXT NOT NULL,
                       email TEXT NOT NULL,
                       role TEXT NOT NULL DEFAULT 'student',
                       profile_image_path TEXT DEFAULT NULL,
                       is_deleted Boolean NOT NULL DEFAULT 0,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                   )
                   ''')
    conn.commit()
    conn.close()
    
def get_users() :
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try : 
        cursor.execute('SELECT * from users_reg WHERE is_deleted = 0 ORDER BY created_at DESC')
        users = cursor.fetchall()
        cursor.close()
        return users
    except sqlite3.Error as e:
        print(f"Database error in get_users: {e}")
        return []

def get_user_by_uuid(uuid):
    if not uuid:
        return None
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT uuid, user_id, first_name, last_name, email, role 
            FROM users_reg 
            WHERE uuid = ? AND is_deleted = 0
        ''', (uuid,))
        row = cursor.fetchone()
        
        if row:
            return {
                "uuid": row[0], 
                'user_id': row[1], 
                'first_name': row[2], 
                'last_name': row[3], 
                'email': row[4], 
                'role': row[5]
            }
        else:
            return None
            
    except sqlite3.Error as e:
        print(f"Database error in get_user_by_uuid: {e}")
        return None
    finally :
        conn.close()

def add_user(uuid , user_id, first_name, last_name, email, role='student') :
    
    if not all([uuid, user_id, first_name, last_name, email]):
        return {"success": False, "message": "กรุณากรอกข้อมูลให้ครบถ้วน"}
    
    if '@' not in email or '.' not in email:
        return {"success": False, "message": "รูปแบบ email ไม่ถูกต้อง"}
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT COUNT(*) FROM users_reg 
            WHERE (uuid = ? OR user_id = ? OR email = ?) AND is_deleted = 0
        ''', (uuid, user_id, email))
        
        if cursor.fetchone()[0] > 0:
            return {"success": False, "message": "UUID, User ID หรือ Email นี้มีอยู่ในระบบแล้ว"}
        
        cursor.execute('''
            INSERT INTO users_reg (uuid, user_id, first_name, last_name, name, email, role, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (uuid, user_id, first_name, last_name, first_name + ' ' + last_name, email, role))
        
        conn.commit()
        user_id_created = cursor.lastrowid
        
        csv_path = os.path.join(os.path.dirname(__file__), '..', 'database', 'users.csv')
        csv_path = os.path.abspath(csv_path)
        file_exists = os.path.isfile(csv_path)

        with open(csv_path, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(['uuid', 'user_id', 'first_name', 'last_name', 'name', 'email', 'role'])
                writer.writerow([uuid, user_id, first_name, last_name, f"{first_name} {last_name}", email, role])
        
        return {
            "success": True, 
            "message": "เพิ่มผู้ใช้สำเร็จ",
            "user_id": user_id_created
        }
        
    except sqlite3.IntegrityError as e:
        return {"success": False, "message": f"ข้อมูลซ้ำกัน: {str(e)}"}
    except sqlite3.Error as e:
        return {"success": False, "message": f"เกิดข้อผิดพลาดในฐานข้อมูล: {str(e)}"}

def delete_user(id) :
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''UPDATE users_reg SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?''', (id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            return {"success": True, "message": "ลบผู้ใช้สำเร็จ"}
        else:
            return {"success": False, "message": "ไม่สามารถลบผู้ใช้ได้"}
                
    except sqlite3.Error as e:
        return {"success": False, "message": f"เกิดข้อผิดพลาด: {str(e)}"}
    finally :
        conn.close()
        
# ✅ Use this for "create user"
def check_is_user_id_exist(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT 1 FROM users_reg WHERE user_id = ? LIMIT 1', (user_id,))
        result = cursor.fetchone()
        if result:
            return {"success": True, "message": "userId นี้มีอยู่แล้ว"}
        return {"success": False, "message": ""}
    except sqlite3.Error as e:
        return {"success": False, "message": f"เกิดข้อผิดพลาด: {str(e)}"}
    finally:
        conn.close()

# ✅ Use this for "update user"
def check_is_user_id_exist_except_id(user_id, current_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT 1 FROM users_reg WHERE user_id = ? AND id != ? LIMIT 1',
            (user_id, current_id)
        )
        result = cursor.fetchone()
        if result:
            return {"success": True, "message": "userId นี้มีอยู่แล้ว"}
        return {"success": False, "message": ""}
    except sqlite3.Error as e:
        return {"success": False, "message": f"เกิดข้อผิดพลาด: {str(e)}"}
    finally:
        conn.close()
        
@app.route('/register')
def index() :
    users = get_users()
    return render_template('index.html', users=users)

@app.route('/api/send_uuid', methods=['POST'])
def get_uuid() :
    global latest_uuid
    data = request.get_json()
    uuid = data.get('uuid')
    latest_uuid = uuid
    
    user = get_user_by_uuid(latest_uuid)
    user_id = user['user_id'] if user else ''
    first_name = user['first_name'] if user else ''
    last_name = user['last_name'] if user else ''
    email = user['email'] if user else ''
    
    socketio.emit('uuid_update', {
        'uuid': latest_uuid,
        'user_id': user_id,
        'first_name': first_name,
        'last_name': last_name,
        'email': email
    })
    
    if (uuid) :
        return jsonify({ "message" : f" This is your {uuid}"})
    else : 
        return jsonify({"error" : "Please scan rfid to get your uuid"}), 400
    
    
@app.route('/api/latest_uuid', methods=['GET'])
def get_latest_uid():
    try:
        print(f"Latest UUID: {latest_uuid}")
        
        if not latest_uuid:
            return jsonify({
                'success': False,
                'message': 'ไม่มี UUID ล่าสุด กรุณาสแกน RFID ก่อน'
            }), 404
        
        user = get_user_by_uuid(latest_uuid)
        
        if user:
            return jsonify({
                'success': True,
                'has_user_data': True,
                'uuid': user['uuid'],
                'user_id': user['user_id'],
                'first_name': user['first_name'],
                'last_name': user['last_name'],
                'email': user['email'],
                'role': user['role'],
                'name': user['name']
            })
        else:
            return jsonify({
                'success': True,
                'has_user_data': False,
                'uuid': latest_uuid,
                'message': 'UUID ยังไม่ได้ลงทะเบียน'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'เกิดข้อผิดพลาด: {str(e)}'
        }), 500

@app.route('/api/reset_uuid', methods=['POST'])
def reset_uuid():
    global latest_uuid
    latest_uuid = None
    return jsonify({ 'success' : True})

# @app.route('/api/upload', methods=['POST'])
# def upload():
#     img_data = request.data
#     if not os.path.exists(PHOTO_DIR):
#         os.makedirs(PHOTO_DIR)
#     image_name = str(uuid4()) + '.jpg'
#     fileName = os.path.join(PHOTO_DIR, image_name)
#     with open(fileName, "wb") as f:
#         f.write(img_data)
        
#     rel_path = f"{PHOTO_DIR}/{image_name}"
#     return jsonify({"success": True, "path_file": rel_path})
   
# @app.route('/photos/<file_name>')
# def serve_photo(file_name):
#     return send_from_directory(PHOTO_DIR, file_name)
    
@app.route('/api/add_user', methods=['POST']) 
def add_user_route():
    global latest_uuid
    
    try:
        uuid = request.form.get('uuid', '').strip()
        user_id = request.form.get('user_id', '').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', 'student').strip()

        if not all([uuid, user_id, first_name, last_name, email]):
            return jsonify({'message': 'กรุณากรอกข้อมูลให้ครบถ้วน'}), 400
        
        is_user_id_exist = check_is_user_id_exist(user_id)
        
        if is_user_id_exist["success"]:
            return jsonify({'success' : False, 'message' : is_user_id_exist['message']})
        
        result = add_user(uuid, user_id, first_name, last_name, email, role)
        
        if result["success"]:
            latest_uuid = None 
            return jsonify({
                'success': result["success"],
                'message': result["message"],
                'user_id': result["user_id"]
            }), 201
        else:
            return jsonify({'success': result['success'], 'message': result["message"]}), 409
            
    except KeyError as e:
        return jsonify({'success' : False,'message': f'ขาดข้อมูล: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'success' : False,'message': f'เกิดข้อผิดพลาด: {str(e)}'}), 500

@app.route('/api/delete_user/<int:id>', methods=['GET'])
def delete_user_route(id) :
    delete_user(id)
    return redirect(url_for('index'))

@app.route('/api/edit_user/<int:id>', methods=['GET'])
def edit_user_route(id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT id, uuid, user_id, first_name, last_name, email FROM users_reg WHERE id = ?', (id,))
    user = cursor.fetchone()
    conn.close() 
    if user:
        return render_template('edit_user.html', user=user)
    return redirect(url_for('index'))

@app.route('/api/update_user/<int:id>', methods=['POST'])
def update_user_route(id):
    try:
        uuid = request.form['uuid']
        user_id = request.form['userId']
        first_name = request.form['firstName']
        last_name = request.form['lastName']
        email = request.form['email']
        
        is_user_id_exist = check_is_user_id_exist_except_id(user_id, id)
        
        if is_user_id_exist["success"]:
            return jsonify({'success' : False, 'message' : is_user_id_exist['message']})
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE users_reg 
            SET uuid = ?, user_id = ?, first_name = ?, last_name = ?, email = ? 
            WHERE id = ?
            ''',
            (uuid, user_id, first_name, last_name, email, id)
        )
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": "แก้ไขข้อมูล user สำเร็จ"})
    
    except sqlite3.Error as e:
        return jsonify({"success": False, "message": f"Database error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route('/api/table', methods=['GET'])
def table_route():
    users = get_users()
    return render_template('table.html', users=users)
    
if __name__ == '__main__' :
    init_db()
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)