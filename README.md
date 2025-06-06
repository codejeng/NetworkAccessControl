# ESP32 RFID Door Access System Setup Guide

## Overview
This system consists of:
- **ESP32 with RFID scanner** - Local access control with offline capability
- **Flask server** - Backend API and web dashboard
- **SQLite database** - User and access log storage

## Hardware Requirements

### ESP32 Setup
```
ESP32 Pin Connections:
- MFRC522 RFID Reader:
  * SDA/SS  -> GPIO 5
  * SCK     -> GPIO 18
  * MOSI    -> GPIO 23
  * MISO    -> GPIO 19
  * IRQ     -> Not connected
  * GND     -> GND
  * RST     -> GPIO 13
  * 3.3V    -> 3.3V

- Door Control:
  * Relay   -> GPIO 2
  * Green LED -> GPIO 4
  * Red LED -> GPIO 15
  * Buzzer  -> GPIO 16
```

## Software Requirements

### Arduino Libraries (ESP32)
Install these libraries in Arduino IDE:
```
- MFRC522 by GithubCommunity (v1.4.10+)
- ArduinoJson by Benoit Blanchon (v6.19.4+)
- WiFi (built-in ESP32)
- HTTPClient (built-in ESP32)
- Preferences (built-in ESP32)
```

### Python Requirements (Server)
```bash
pip install flask sqlite3
```

Or create `requirements.txt`:
```
Flask==2.3.3
```

## Installation Steps

### 1. Server Setup
```bash
# Clone or download the server code
mkdir rfid_server
cd rfid_server

# Save the Flask code as 'app.py'
# Install requirements
pip install -r requirements.txt

# Run the server
python app.py
```

### 2. ESP32 Setup
1. Open Arduino IDE
2. Install ESP32 board support
3. Install required libraries
4. Update WiFi credentials in the code:
   ```cpp
   const char* ssid = "YOUR_WIFI_NAME";
   const char* password = "YOUR_WIFI_PASSWORD";
   const char* room = "YOUR_ROOM";
   ```
5. Update server IP address:
   ```cpp
   const char* serverURL = "http://YOUR_SERVER_IP:5000";
   ```
6. Upload code to ESP32

### 3. Hardware Assembly
1. Connect RFID reader to ESP32 as shown in pin diagram
2. Connect relay for door lock control
3. Connect LEDs and buzzer for feedback
4. Power the ESP32 (USB or external 5V)

## System Flow

### Access Control Logic
```
1. RFID card detected
2. Check local storage first
   - If found and has access → Grant access
   - If found but no access → Deny access
   - If not found → Check server
3. If not in local storage:
   - Send request to server
   - If server grants access → Add to local storage and grant
   - If server denies → Deny access
4. Log access attempt to server
5. Provide visual/audio feedback
```

### Server Synchronization
- ESP32 syncs with server every 5 minutes
- Downloads all user cards to local storage
- Enables offline operation when WiFi is unavailable
- Logs all access attempts when connection is restored

## API Endpoints

### ESP32 → Server
- `POST /api/check_access` - Check if RFID has access
- `POST /api/sync_cards` - Download all cards for offline storage
- `POST /api/log_access` - Log access attempts

### Web Dashboard
- `GET /` - Main dashboard interface
- `GET /api/users` - Get all users
- `POST /api/users` - Add new user
- `PUT /api/users/{id}/access` - Update user access
- `DELETE /api/users/{id}` - Delete user
- `GET /api/access_logs` - Get access logs
- `GET /api/stats` - Get system statistics

## Database Schema

### Users Table
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    rfid_uid TEXT UNIQUE NOT NULL,
    has_access BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Access Logs Table
```sql
CREATE TABLE access_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rfid_uid TEXT NOT NULL,
    access_granted BOOLEAN NOT NULL,
    device_id TEXT,
    device_ip TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);
```

## Configuration

### ESP32 Configuration
Update these variables in the Arduino code:
```cpp
// WiFi settings
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// Server settings
const char* serverURL = "http://192.168.1.100:5000";

// Hardware settings
#define RELAY_PIN 2      // Door lock relay
#define LED_GREEN 4      // Access granted LED
#define LED_RED 15       // Access denied LED
#define BUZZER_PIN 16    // Buzzer

// Timing settings
const unsigned long SYNC_INTERVAL = 300000;        // 5 minutes
const unsigned long DOOR_OPEN_DURATION = 3000;     // 3 seconds
```

### Server Configuration
Update these settings in the Flask app:
```python
# Database file
DATABASE = 'rfid_access.db'

# Server settings
app.run(host='0.0.0.0', port=5000, debug=True)
```

## Usage

### Adding New Users
1. Open web dashboard: `http://server_ip:5000`
2. Click "Add New User"
3. Enter name and RFID UID
4. Set access permission
5. Save user

### Monitoring Access
- View real-time access logs in dashboard
- Monitor system statistics
- Track successful/failed access attempts

### Managing Users
- Grant/revoke access permissions
- Delete users
- View user creation dates

## Troubleshooting

### Common Issues

**ESP32 won't connect to WiFi:**
- Check SSID and password
- Ensure WiFi network is 2.4GHz
- Check signal strength

**RFID not reading:**
- Verify wiring connections
- Check power supply (3.3V)
- Test with known working cards

**Server connection failed:**
- Verify server IP address
- Check firewall settings
- Ensure server is running

**Door not opening:**
- Check relay connections
- Verify relay trigger voltage
- Test relay manually

### Debug Output
Enable serial monitor at 115200 baud to see:
- WiFi connection status
- RFID card detection
- Server communication
- Access decisions
- Error messages

## Security Considerations

1. **Network Security:**
   - Use WPA2/WPA3 WiFi encryption
   - Consider VPN for remote access
   - Firewall server ports appropriately

2. **Physical Security:**
   - Secure ESP32 in tamper-proof enclosure
   - Use backup power supply
   - Monitor system status remotely

3. **Access Control:**
   - Regular audit of user permissions
   - Monitor access logs for anomalies
   - Implement access time restrictions (future enhancement)
