#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <ESPAsyncWebServer.h>
#include <AsyncJson.h>

// Configuration - Modify these values for each room
const char* ssid = "NAC";
const char* password = "12345678NAC";
const char* serverURL = "http://192.168.1.115:5000";
const char* room = "EN4401";  // Change this for each ESP32 device

const int httpTimeout = 5000;

// Hardware pins
#define RST_PIN 13
#define SS_PIN 5
#define RELAY_PIN 2    // Door lock relay
#define LED_GREEN 4    // Access granted LED
#define LED_RED 15     // Access denied LED
#define BUZZER_PIN 16  // Buzzer

// Create instances
MFRC522 mfrc522(SS_PIN, RST_PIN);
Preferences preferences;
AsyncWebServer server(80);  // Create web server on port 80

// Permanent access storage for active reservations
struct TempAccess {
  char uid[17];        // RFID UID
  char name[32];       // User name
  bool isActive;       // Whether this slot is active
};

TempAccess tempUsers[20];  // Store up to 20 temporary users
const int MAX_TEMP_USERS = 20;
int tempUserCount = 0;

// System variables
unsigned long doorOpenTime = 0;
const unsigned long DOOR_OPEN_DURATION = 3000;  // 3 seconds
bool doorIsOpen = false;

// Server connection tracking
bool serverConnected = false;
unsigned long lastServerRegister = 0;
const unsigned long REGISTER_INTERVAL = 300000;  // Register room every 5 minutes

void setup() {
  Serial.begin(115200);
  while (!Serial);

  Serial.println("===========================================");
  Serial.println("   ESP32 RFID Room Access Control System");
  Serial.println("===========================================");
  Serial.printf("🏢 Room: %s\n", room);

  // Initialize hardware
  initializeHardware();

  // Initialize SPI and RFID
  SPI.begin();
  mfrc522.PCD_Init();

  // Connect to WiFi
  connectToWiFi();

  // Setup web server routes
  setupWebServer();

  // Register this room with server
  registerRoomWithServer();

  // Initialize temporary user storage
  initializeTempStorage();

  // System ready
  signalReady();
  Serial.println("\n🚪 Room Access System Ready!");
  Serial.println("🔖 Place RFID card near reader...");
  Serial.printf("🌐 Web server running at: http://%s\n", WiFi.localIP().toString().c_str());
}

void loop() {
  // Check WiFi connection
  if (WiFi.status() != WL_CONNECTED) {
    reconnectWiFi();
  }

  // Periodic room registration
  if (millis() - lastServerRegister > REGISTER_INTERVAL) {
    registerRoomWithServer();
    lastServerRegister = millis();
  }

  // Handle door timing
  if (doorIsOpen && millis() - doorOpenTime > DOOR_OPEN_DURATION) {
    closeDoor();
  }

  // Handle serial commands
  handleSerialCommands();

  // RFID card detection
  if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
    processRFIDCard();
    mfrc522.PICC_HaltA();
    mfrc522.PCD_StopCrypto1();
  }

  delay(100);
}

void setupWebServer() {
  Serial.println("🌐 Setting up web server...");
  
  // CORS Headers
  DefaultHeaders::Instance().addHeader("Access-Control-Allow-Origin", "*");
  DefaultHeaders::Instance().addHeader("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
  DefaultHeaders::Instance().addHeader("Access-Control-Allow-Headers", "Content-Type");

  // Handle preflight OPTIONS requests
  server.onNotFound([](AsyncWebServerRequest *request) {
    if (request->method() == HTTP_OPTIONS) {
      request->send(200);
    } else {
      request->send(404, "application/json", "{\"error\":\"Not found\"}");
    }
  });

  // API endpoint to remove user from temp cache
  server.addHandler(new AsyncCallbackJsonWebHandler("/api/remove_user", [](AsyncWebServerRequest *request, JsonVariant &json) {
    Serial.println("📥 Received remove user request");
    
    if (!json.is<JsonObject>()) {
      Serial.println("❌ Invalid JSON received");
      request->send(400, "application/json", "{\"success\":false,\"message\":\"Invalid JSON\"}");
      return;
    }
    
    JsonObject jsonObj = json.as<JsonObject>();
    
    if (!jsonObj.containsKey("rfid_uid")) {
      Serial.println("❌ Missing rfid_uid in request");
      request->send(400, "application/json", "{\"success\":false,\"message\":\"Missing rfid_uid\"}");
      return;
    }
    
    String rfid_uid = jsonObj["rfid_uid"].as<String>();
    String action = jsonObj["action"].as<String>();
    
    Serial.printf("🗑️ Remove user request - UID: %s, Action: %s\n", rfid_uid.c_str(), action.c_str());
    
    // Remove user from temporary cache
    bool removed = removeTempUserByUID(rfid_uid);
    
    if (removed) {
      Serial.printf("✅ Successfully removed user: %s\n", rfid_uid.c_str());
      
      // Send success response
      String response = "{\"success\":true,\"message\":\"User removed successfully\",\"rfid_uid\":\"" + rfid_uid + "\"}";
      request->send(200, "application/json", response);
      
      // Visual feedback for removal
      signalUserRemoved();
    } else {
      Serial.printf("❌ User not found in temp cache: %s\n", rfid_uid.c_str());
      
      // Send not found response
      String response = "{\"success\":false,\"message\":\"User not found in cache\",\"rfid_uid\":\"" + rfid_uid + "\"}";
      request->send(404, "application/json", response);
    }
  }));

  // API endpoint to get current temp users status
  server.on("/api/status", HTTP_GET, [](AsyncWebServerRequest *request) {
    Serial.println("📊 Status request received");
    
    DynamicJsonDocument doc(2048);
    doc["room"] = room;
    doc["ip_address"] = WiFi.localIP().toString();
    doc["mac_address"] = WiFi.macAddress();
    doc["wifi_connected"] = (WiFi.status() == WL_CONNECTED);
    doc["server_connected"] = serverConnected;
    doc["door_open"] = doorIsOpen;
    doc["uptime_ms"] = millis();
    
    // Add temp users info
    JsonArray tempUsersArray = doc.createNestedArray("temp_users");
    int activeCount = 0;
    
    for (int i = 0; i < MAX_TEMP_USERS; i++) {
      if (tempUsers[i].isActive) {
        JsonObject user = tempUsersArray.createNestedObject();
        user["uid"] = tempUsers[i].uid;
        user["name"] = tempUsers[i].name;
        user["permanent"] = true;  // Indicate these are permanent until removed
        activeCount++;
      }
    }
    
    doc["active_temp_users"] = activeCount;
    doc["max_temp_users"] = MAX_TEMP_USERS;
    
    String response;
    serializeJson(doc, response);
    
    request->send(200, "application/json", response);
  });

  // API endpoint to clear all temp users
  server.on("/api/clear_all", HTTP_POST, [](AsyncWebServerRequest *request) {
    Serial.println("🗑️ Clear all users request received");
    
    int clearedCount = getActiveTempUserCount();
    clearAllTempUsers();
    
    String response = "{\"success\":true,\"message\":\"All temp users cleared\",\"cleared_count\":" + String(clearedCount) + "}";
    request->send(200, "application/json", response);
  });

  // Start server
  server.begin();
  Serial.println("✅ Web server started successfully");
  Serial.printf("🌐 Listening on: http://%s\n", WiFi.localIP().toString().c_str());
}

void initializeHardware() {
  // Initialize pins
  pinMode(RELAY_PIN, OUTPUT);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_RED, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  // Initial states
  digitalWrite(RELAY_PIN, LOW);  // Door locked
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_RED, LOW);
  digitalWrite(BUZZER_PIN, LOW);

  // Initialize preferences (flash storage)
  preferences.begin("room_access", false);
  
  Serial.println("✅ Hardware initialized successfully");
}

void connectToWiFi() {
  Serial.println("\n🌐 Connecting to WiFi...");
  Serial.printf("📡 SSID: %s\n", ssid);
  
  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi Connected Successfully!");
    Serial.printf("📡 IP Address: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("📶 Signal Strength: %d dBm\n", WiFi.RSSI());
    Serial.printf("🔧 MAC Address: %s\n", WiFi.macAddress().c_str());
  } else {
    Serial.println("\n❌ WiFi Connection Failed!");
    Serial.printf("❌ Failed after %d attempts\n", attempts);
  }
}

void reconnectWiFi() {
  static unsigned long lastAttempt = 0;
  if (millis() - lastAttempt > 10000) {
    Serial.println("🔄 WiFi disconnected, attempting reconnection...");
    WiFi.disconnect();
    WiFi.begin(ssid, password);
    lastAttempt = millis();
  }
}

void registerRoomWithServer() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ Cannot register room - No WiFi connection");
    return;
  }

  Serial.println("🏢 Registering room with server...");
  Serial.printf("🌐 Connecting to: %s/api/esp32/register\n", serverURL);

  HTTPClient http;
  String url = String(serverURL) + "/api/esp32/register";
  http.begin(url);
  http.setTimeout(httpTimeout);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(512);
  doc["room"] = room;
  doc["mac_address"] = WiFi.macAddress();
  doc["ip_address"] = WiFi.localIP().toString();

  String payload;
  serializeJson(doc, payload);
  
  Serial.printf("📤 Registration request: %s\n", payload.c_str());

  unsigned long startTime = millis();
  int responseCode = http.POST(payload);
  unsigned long responseTime = millis() - startTime;

  Serial.printf("📊 Response time: %lu ms\n", responseTime);
  Serial.printf("📡 HTTP Response Code: %d\n", responseCode);

  if (responseCode == 200) {
    String response = http.getString();
    Serial.printf("📥 Server response: %s\n", response.c_str());
    
    DynamicJsonDocument responseDoc(1024);
    if (deserializeJson(responseDoc, response) == DeserializationError::Ok) {
      if (responseDoc["success"]) {
        Serial.println("✅ Room registered successfully!");
        serverConnected = true;
      } else {
        Serial.printf("❌ Registration failed: %s\n", responseDoc["message"].as<String>().c_str());
      }
    }
  } else if (responseCode > 0) {
    Serial.printf("❌ Registration failed - HTTP %d\n", responseCode);
    String errorResponse = http.getString();
    Serial.printf("❌ Error details: %s\n", errorResponse.c_str());
    serverConnected = false;
  } else {
    Serial.printf("❌ Connection failed - Error: %s\n", http.errorToString(responseCode).c_str());
    serverConnected = false;
  }

  http.end();
}

void processRFIDCard() {
  // Get card UID
  String cardUID = "";
  for (byte i = 0; i < mfrc522.uid.size; i++) {
    if (mfrc522.uid.uidByte[i] < 0x10) cardUID += "0";
    cardUID += String(mfrc522.uid.uidByte[i], HEX);
  }
  cardUID.toUpperCase();

  Serial.printf("\n🔖 Card detected: %s\n", cardUID.c_str());

  // Step 1: Check ESP's temporary table first
  if (checkTempAccess(cardUID)) {
    Serial.println("✅ Access granted from ESP table");
    grantAccess(cardUID);
    return;
  }

  Serial.println("❓ Card not found in ESP table, checking server...");

  // Step 2: Check server for active reservation
  if (checkServerReservation(cardUID)) {
    Serial.println("✅ Active reservation found, access granted");
    grantAccess(cardUID);
  } else {
    Serial.println("❌ No active reservation found, access denied");
    denyAccess(cardUID);
  }
}

bool checkTempAccess(String uid) {
  Serial.printf("🔍 Checking ESP temp table for UID: %s\n", uid.c_str());
  
  for (int i = 0; i < MAX_TEMP_USERS; i++) {
    if (tempUsers[i].isActive && uid.equals(tempUsers[i].uid)) {
      Serial.printf("💾 Found in ESP table: %s (Permanent access)\n", tempUsers[i].name);
      return true;
    }
  }
  
  Serial.println("💾 Card not found in ESP table");
  return false;
}

bool checkServerReservation(String uid) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ No WiFi connection, cannot check server");
    return false;
  }

  Serial.println("🌐 Checking server for active reservation...");
  Serial.printf("🔗 Server URL: %s/api/esp32/check_access\n", serverURL);

  HTTPClient http;
  String url = String(serverURL) + "/api/esp32/check_access";
  http.begin(url);
  http.setTimeout(httpTimeout);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(512);
  doc["rfid_uid"] = uid;
  doc["room"] = room;
  doc["mac_address"] = WiFi.macAddress();

  String payload;
  serializeJson(doc, payload);
  
  Serial.printf("📤 Access check: %s\n", payload.c_str());

  unsigned long startTime = millis();
  int responseCode = http.POST(payload);
  unsigned long responseTime = millis() - startTime;

  Serial.printf("📊 Response time: %lu ms\n", responseTime);
  Serial.printf("📡 HTTP Response Code: %d\n", responseCode);

  if (responseCode == 200) {
    String response = http.getString();
    Serial.printf("📥 Server response: %s\n", response.c_str());
    
    DynamicJsonDocument responseDoc(1024);

    if (deserializeJson(responseDoc, response) == DeserializationError::Ok) {
      bool accessGranted = responseDoc["access_granted"];
      
      if (accessGranted) {
        String userName = responseDoc["user_name"] | "Unknown";
        bool cacheUser = responseDoc["cache_user"] | false;
        
        Serial.printf("✅ Access granted!\n");
        Serial.printf("👤 User: %s\n", userName.c_str());
        
        // If server says to cache user, add them to ESP permanent table
        if (cacheUser) {
          addTempUser(uid, userName);
          Serial.println("💾 User added to ESP permanent access table");
        }
        
        serverConnected = true;
        http.end();
        return true;
      } else {
        String message = responseDoc["message"] | "Access denied";
        Serial.printf("❌ Access denied: %s\n", message.c_str());
      }
    } else {
      Serial.println("❌ Failed to parse server response JSON");
    }
  } else if (responseCode > 0) {
    Serial.printf("❌ Server error - HTTP %d\n", responseCode);
    String errorResponse = http.getString();
    Serial.printf("❌ Error details: %s\n", errorResponse.c_str());
  } else {
    Serial.printf("❌ Connection failed - Error: %s\n", http.errorToString(responseCode).c_str());
  }

  serverConnected = false;
  http.end();
  return false;
}

void addTempUser(String uid, String name) {
  Serial.printf("💾 Adding user to ESP permanent table: %s (%s)\n", name.c_str(), uid.c_str());
  
  // Check if user already exists
  for (int i = 0; i < MAX_TEMP_USERS; i++) {
    if (tempUsers[i].isActive && uid.equals(tempUsers[i].uid)) {
      Serial.printf("⚠️ User already exists in table: %s\n", name.c_str());
      return;
    }
  }
  
  // Find empty slot
  int slotIndex = -1;
  for (int i = 0; i < MAX_TEMP_USERS; i++) {
    if (!tempUsers[i].isActive) {
      slotIndex = i;
      break;
    }
  }
  
  if (slotIndex == -1) {
    Serial.println("⚠️ ESP temp table full! Cannot add new user.");
    return;
  }
  
  // Copy UID and name
  strncpy(tempUsers[slotIndex].uid, uid.c_str(), 16);
  tempUsers[slotIndex].uid[16] = '\0';
  strncpy(tempUsers[slotIndex].name, name.c_str(), 31);
  tempUsers[slotIndex].name[31] = '\0';
  
  // Set as active (permanent until removed by server)
  tempUsers[slotIndex].isActive = true;
  
  // Update count
  if (slotIndex >= tempUserCount) {
    tempUserCount = slotIndex + 1;
  }
  
  Serial.printf("✅ User added to ESP permanent table (slot %d)\n", slotIndex);
  Serial.printf("🔒 Access is permanent until server removes user\n");
  Serial.printf("📊 Active temp users: %d/%d\n", getActiveTempUserCount(), MAX_TEMP_USERS);
}

void removeTempUser(int index) {
  if (index >= 0 && index < MAX_TEMP_USERS && tempUsers[index].isActive) {
    Serial.printf("🗑️ Removing user: %s (%s)\n", 
                 tempUsers[index].name, tempUsers[index].uid);
    
    tempUsers[index].isActive = false;
    memset(&tempUsers[index], 0, sizeof(TempAccess));
    
    Serial.printf("📊 Active temp users: %d/%d\n", getActiveTempUserCount(), MAX_TEMP_USERS);
  }
}

bool removeTempUserByUID(String uid) {
  Serial.printf("🔍 Searching for user to remove: %s\n", uid.c_str());
  
  for (int i = 0; i < MAX_TEMP_USERS; i++) {
    if (tempUsers[i].isActive && uid.equals(tempUsers[i].uid)) {
      String removedName = String(tempUsers[i].name);
      removeTempUser(i);
      Serial.printf("✅ Removed user: %s (%s)\n", removedName.c_str(), uid.c_str());
      return true;
    }
  }
  
  Serial.printf("❌ User not found: %s\n", uid.c_str());
  return false;
}

int getActiveTempUserCount() {
  int count = 0;
  for (int i = 0; i < MAX_TEMP_USERS; i++) {
    if (tempUsers[i].isActive) {
      count++;
    }
  }
  return count;
}

void grantAccess(String uid) {
  Serial.printf("🚪 GRANTING ACCESS for card: %s\n", uid.c_str());
  
  // Visual and audio feedback
  digitalWrite(LED_GREEN, HIGH);
  tone(BUZZER_PIN, 1000, 200);
  delay(200);
  tone(BUZZER_PIN, 1500, 200);

  // Open door
  openDoor();

  delay(1000);
  digitalWrite(LED_GREEN, LOW);
  
  // Log access to server
  logAccessToServer(uid, true);
}

void denyAccess(String uid) {
  Serial.printf("🚫 DENYING ACCESS for card: %s\n", uid.c_str());
  
  // Visual and audio feedback
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_RED, HIGH);
    tone(BUZZER_PIN, 300, 200);
    delay(200);
    digitalWrite(LED_RED, LOW);
    delay(200);
  }
  
  // Log access to server
  logAccessToServer(uid, false);
}

void openDoor() {
  Serial.println("🚪 Opening door...");
  digitalWrite(RELAY_PIN, HIGH);  // Unlock door
  doorIsOpen = true;
  doorOpenTime = millis();
}

void closeDoor() {
  Serial.println("🔒 Closing door...");
  digitalWrite(RELAY_PIN, LOW);  // Lock door
  doorIsOpen = false;
}

void logAccessToServer(String uid, bool granted) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ Cannot log access - No WiFi connection");
    return;
  }

  Serial.println("📝 Logging access attempt to server...");

  HTTPClient http;
  String url = String(serverURL) + "/api/esp32/log_access";
  http.begin(url);
  http.setTimeout(httpTimeout);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(1024);
  doc["rfid_uid"] = uid;
  doc["room"] = room;
  doc["access_granted"] = granted;
  doc["mac_address"] = WiFi.macAddress();
  doc["ip_address"] = WiFi.localIP().toString();

  String payload;
  serializeJson(doc, payload);
  
  int responseCode = http.POST(payload);
  
  if (responseCode == 200) {
    Serial.println("✅ Access logged successfully");
  } else {
    Serial.printf("❌ Failed to log access - HTTP %d (This endpoint may not exist)\n", responseCode);
  }
  
  http.end();
}

void initializeTempStorage() {
  Serial.println("💾 Initializing permanent user storage...");
  
  // Clear all temporary users
  memset(tempUsers, 0, sizeof(tempUsers));
  tempUserCount = 0;
  
  for (int i = 0; i < MAX_TEMP_USERS; i++) {
    tempUsers[i].isActive = false;
  }
  
  Serial.printf("✅ Permanent storage initialized (%d slots available)\n", MAX_TEMP_USERS);
}

void logTempUserStatus() {
  Serial.println("\n======================================");
  Serial.println("       PERMANENT USER STATUS");
  Serial.println("======================================");
  
  int activeCount = getActiveTempUserCount();
  Serial.printf("📊 Active Users: %d/%d\n", activeCount, MAX_TEMP_USERS);
  Serial.printf("🏢 Room: %s\n", room);
  Serial.printf("⏰ Current Time: %lu ms\n", millis());
  
  if (activeCount > 0) {
    Serial.println("\n📋 Currently Active Users:");
    Serial.println("--------------------------------------");
    
    for (int i = 0; i < MAX_TEMP_USERS; i++) {
      if (tempUsers[i].isActive) {
        Serial.printf("%2d. %s (%s) - PERMANENT ACCESS\n", 
                     i+1, tempUsers[i].name, tempUsers[i].uid);
      }
    }
    Serial.println("--------------------------------------");
  } else {
    Serial.println("✅ No active permanent users");
  }
  
  Serial.println("======================================\n");
}

void signalReady() {
  Serial.println("🎵 System ready signal...");
  
  // Signal system is ready
  for (int i = 0; i < 2; i++) {
    digitalWrite(LED_GREEN, HIGH);
    tone(BUZZER_PIN, 2000, 100);
    delay(100);
    digitalWrite(LED_GREEN, LOW);
    delay(100);
  }
  
  Serial.println("✅ Ready signal completed");
}

void signalUserRemoved() {
  Serial.println("🎵 User removed signal...");
  
  // Signal user was removed from cache
  for (int i = 0; i < 2; i++) {
    digitalWrite(LED_RED, HIGH);
    tone(BUZZER_PIN, 800, 150);
    delay(150);
    digitalWrite(LED_RED, LOW);
    delay(150);
    tone(BUZZER_PIN, 600, 150);
    delay(150);
  }
  
  Serial.println("✅ User removed signal completed");
}

void handleSerialCommands() {
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    command.toLowerCase();
    
    if (command.equals("status")) {
      logTempUserStatus();
      
    } else if (command.equals("clear")) {
      clearAllTempUsers();
      
    } else if (command.startsWith("remove ")) {
      String uid = command.substring(7);
      uid.toUpperCase();
      removeTempUserByUID(uid);
      
    } else if (command.equals("register")) {
      registerRoomWithServer();
      
    } else if (command.equals("help")) {
      printSerialHelp();
      
    } else if (command.length() > 0) {
      Serial.printf("❌ Unknown command: %s\n", command.c_str());
      Serial.println("Type 'help' for available commands");
    }
  }
}

void clearAllTempUsers() {
  Serial.println("🗑️ Clearing all permanent users...");
  
  int clearedCount = getActiveTempUserCount();
  initializeTempStorage();
  
  Serial.printf("✅ Cleared %d permanent users\n", clearedCount);
  
  // Visual feedback
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_RED, HIGH);
    tone(BUZZER_PIN, 1000, 200);
    delay(200);
    digitalWrite(LED_RED, LOW);
    delay(200);
  }
}

void printSerialHelp() {
  Serial.println("\n=== ROOM ACCESS CONTROL COMMANDS ===");
  Serial.println("status                - Show current permanent users");
  Serial.println("clear                 - Clear all permanent users");
  Serial.println("remove <UID>          - Remove user by UID");
  Serial.println("register              - Re-register room with server");
  Serial.println("help                  - Show this help menu");
  Serial.println("=====================================");
  Serial.println("\n=== WEB API ENDPOINTS ===");
  Serial.printf("GET  http://%s/api/status        - Get system status\n", WiFi.localIP().toString().c_str());
  Serial.printf("POST http://%s/api/remove_user   - Remove user from cache\n", WiFi.localIP().toString().c_str());
  Serial.printf("POST http://%s/api/clear_all     - Clear all temp users\n", WiFi.localIP().toString().c_str());
  Serial.println("=====================================\n");
}