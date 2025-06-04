#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>

// WiFi credentials
const char* ssid = "Fahsai";
const char* password = "12345678";

// Server configuration
const char* serverURL = "http://192.168.113.199:5000";
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

// Local RFID storage (maximum 100 cards) - Optimized structure
struct RFIDCard {
  char uid[17];        // Fixed size for UID (16 chars + null terminator)
  char name[32];       // Fixed size for name (31 chars + null terminator)
  bool hasAccess;
  uint32_t addedTime;  // Use uint32_t instead of unsigned long to save memory
};

RFIDCard localCards[100];
int cardCount = 0;
const int MAX_CARDS = 100;

// System variables
unsigned long lastServerSync = 0;
const unsigned long SYNC_INTERVAL = 300000;  // 5 minutes
unsigned long doorOpenTime = 0;
const unsigned long DOOR_OPEN_DURATION = 3000;  // 3 seconds
bool doorIsOpen = false;

// Server connection tracking
bool serverConnected = false;
unsigned long lastServerCheck = 0;
int serverFailureCount = 0;

// Storage logging variables
unsigned long lastStorageLog = 0;
const unsigned long STORAGE_LOG_INTERVAL = 120000;  // Log every 2 minutes (reduced frequency)
unsigned long lastStorageReport = 0;
const unsigned long STORAGE_REPORT_INTERVAL = 900000;  // Report to server every 15 minutes (reduced frequency)

void setup() {
  Serial.begin(115200);
  while (!Serial)
    ;

  Serial.println("===========================================");
  Serial.println("    ESP32 RFID Door Access System");
  Serial.println("===========================================");

  // Initialize hardware
  initializeHardware();

  // Initialize SPI and RFID
  SPI.begin();
  mfrc522.PCD_Init();

  // Load stored cards from flash memory
  loadStoredCards();

  // Connect to WiFi
  connectToWiFi();

  // Initial server sync
  syncWithServer();

  // Log initial storage status
  logStorageStatus(true);

  // System ready
  signalReady();
  Serial.println("\n🚪 Door Access System Ready!");
  Serial.printf("📊 Loaded %d cards from memory\n", cardCount);
  Serial.println("🔖 Place RFID card near reader...");
}

void loop() {
  // Check WiFi connection
  if (WiFi.status() != WL_CONNECTED) {
    reconnectWiFi();
  }

  // Periodic server sync
  if (millis() - lastServerSync > SYNC_INTERVAL) {
    syncWithServer();
    lastServerSync = millis();
  }

  // Periodic storage logging
  if (millis() - lastStorageLog > STORAGE_LOG_INTERVAL) {
    logStorageStatus(false);
    lastStorageLog = millis();
  }

  handleSerialCommands();

  // Periodic storage report to server
  if (millis() - lastStorageReport > STORAGE_REPORT_INTERVAL) {
    reportStorageToServer();
    lastStorageReport = millis();
  }

  // Handle door timing
  if (doorIsOpen && millis() - doorOpenTime > DOOR_OPEN_DURATION) {
    closeDoor();
  }

  // RFID card detection
  if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
    processRFIDCard();
    mfrc522.PICC_HaltA();
    mfrc522.PCD_StopCrypto1();
  }

  delay(100);
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
  preferences.begin("rfid_cards", false);
  
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
    Serial.printf("🌐 Gateway: %s\n", WiFi.gatewayIP().toString().c_str());
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

void processRFIDCard() {
  // Get card UID
  String cardUID = "";
  for (byte i = 0; i < mfrc522.uid.size; i++) {
    if (mfrc522.uid.uidByte[i] < 0x10) cardUID += "0";
    cardUID += String(mfrc522.uid.uidByte[i], HEX);
  }
  cardUID.toUpperCase();

  Serial.printf("\n🔖 Card detected: %s\n", cardUID.c_str());

  // Check local storage first
  bool accessGranted = checkLocalAccess(cardUID);

  if (accessGranted) {
    Serial.println("✅ Access granted from local storage");
    Serial.printf("💾 Card found in local database\n");
    grantAccess(cardUID);
  } else {
    Serial.println("❓ Card not found locally, checking server...");

    // Check server and update local storage
    if (checkServerAccess(cardUID)) {
      Serial.println("✅ Access granted from server");
      grantAccess(cardUID);
    } else {
      Serial.println("❌ Access denied");
      denyAccess(cardUID);
    }
  }

  // Log access attempt
  logAccessAttempt(cardUID, accessGranted);
  
  // Log storage status after each card interaction
  logStorageStatus(false);
}

bool checkLocalAccess(String uid) {
  Serial.printf("🔍 Checking local storage for UID: %s\n", uid.c_str());
  
  for (int i = 0; i < cardCount; i++) {
    if (uid.equals(localCards[i].uid)) {
      Serial.printf("💾 Found in local storage: %s (Access: %s)\n", 
                   localCards[i].name, 
                   localCards[i].hasAccess ? "GRANTED" : "DENIED");
      return localCards[i].hasAccess;
    }
  }
  
  Serial.println("💾 Card not found in local storage");
  return false;
}

bool checkServerAccess(String uid) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ No WiFi connection, using local data only");
    return false;
  }

  Serial.println("🌐 Attempting server connection...");
  Serial.printf("🔗 Server URL: %s/api/check_access\n", serverURL);

  HTTPClient http;
  String url = String(serverURL) + "/api/check_access";
  http.begin(url);
  http.setTimeout(httpTimeout);
  http.addHeader("Content-Type", "application/json");

  // Create JSON payload
  DynamicJsonDocument doc(512);
  doc["rfid_uid"] = uid;
  doc["device_id"] = WiFi.macAddress();

  String payload;
  serializeJson(doc, payload);
  
  Serial.printf("📤 Sending request: %s\n", payload.c_str());

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
      bool hasAccess = responseDoc["access_granted"];
      String userName = responseDoc["user_name"] | "Unknown";

      Serial.printf("✅ Server connected successfully!\n");
      Serial.printf("👤 User: %s, Access: %s\n", userName.c_str(), hasAccess ? "GRANTED" : "DENIED");
      
      // Add/update in local storage
      addOrUpdateLocalCard(uid, userName, hasAccess);
      
      serverConnected = true;
      serverFailureCount = 0;
      lastServerCheck = millis();

      http.end();
      return hasAccess;
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
  serverFailureCount++;
  Serial.printf("⚠️ Server connection failed (Failure count: %d)\n", serverFailureCount);

  http.end();
  return false;
}

void addOrUpdateLocalCard(String uid, String name, bool access) {
  Serial.printf("💾 Updating local storage for: %s (%s)\n", name.c_str(), uid.c_str());
  
  // Check if card already exists
  for (int i = 0; i < cardCount; i++) {
    if (uid.equals(localCards[i].uid)) {
      String oldName = String(localCards[i].name);
      bool oldAccess = localCards[i].hasAccess;
      
      // Update existing card
      strncpy(localCards[i].name, name.c_str(), 31);
      localCards[i].name[31] = '\0';  // Ensure null termination
      localCards[i].hasAccess = access;
      
      Serial.printf("💾 Updated existing card:\n");
      Serial.printf("   - Name: %s -> %s\n", oldName.c_str(), localCards[i].name);
      Serial.printf("   - Access: %s -> %s\n", oldAccess ? "GRANTED" : "DENIED", access ? "GRANTED" : "DENIED");
      
      saveStoredCards();
      logStorageStatus(false);
      return;
    }
  }

  // Add new card if space available
  if (cardCount < MAX_CARDS) {
    // Copy UID (ensure it fits)
    strncpy(localCards[cardCount].uid, uid.c_str(), 16);
    localCards[cardCount].uid[16] = '\0';  // Ensure null termination
    
    // Copy name (ensure it fits)
    strncpy(localCards[cardCount].name, name.c_str(), 31);
    localCards[cardCount].name[31] = '\0';  // Ensure null termination
    
    localCards[cardCount].hasAccess = access;
    localCards[cardCount].addedTime = (uint32_t)(millis() / 1000);  // Store in seconds to save space
    cardCount++;
    
    Serial.printf("💾 Added new card to local storage:\n");
    Serial.printf("   - UID: %s\n", localCards[cardCount-1].uid);
    Serial.printf("   - Name: %s\n", localCards[cardCount-1].name);
    Serial.printf("   - Access: %s\n", access ? "GRANTED" : "DENIED");
    Serial.printf("   - Total cards in storage: %d/%d\n", cardCount, MAX_CARDS);
    
    saveStoredCards();
    logStorageStatus(false);
  } else {
    Serial.printf("⚠️ Local storage full! Cannot add card: %s\n", uid.c_str());
    logStorageWarning();
  }
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

void syncWithServer() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ Cannot sync - No WiFi connection");
    return;
  }

  Serial.println("🔄 Starting server synchronization...");
  Serial.printf("🌐 Connecting to: %s/api/sync_cards\n", serverURL);

  HTTPClient http;
  String url = String(serverURL) + "/api/sync_cards";
  http.begin(url);
  http.setTimeout(httpTimeout);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(512);
  doc["device_id"] = WiFi.macAddress();
  doc["last_sync"] = lastServerSync;

  String payload;
  serializeJson(doc, payload);
  
  Serial.printf("📤 Sync request: %s\n", payload.c_str());

  unsigned long startTime = millis();
  int responseCode = http.POST(payload);
  unsigned long responseTime = millis() - startTime;

  Serial.printf("📊 Sync response time: %lu ms\n", responseTime);
  Serial.printf("📡 HTTP Response Code: %d\n", responseCode);

  if (responseCode == 200) {
    String response = http.getString();
    Serial.printf("📥 Sync response: %s\n", response.c_str());
    
    DynamicJsonDocument responseDoc(2048);

    if (deserializeJson(responseDoc, response) == DeserializationError::Ok) {
      JsonArray cards = responseDoc["cards"];
      int oldCardCount = cardCount;

      Serial.printf("🔄 Received %d cards from server\n", cards.size());

      // Clear local storage and reload from server
      cardCount = 0;

      for (JsonObject card : cards) {
        String uid = card["rfid_uid"];
        String name = card["user_name"];
        bool access = card["has_access"];

        if (cardCount < MAX_CARDS) {
          // Copy UID (ensure it fits)
          strncpy(localCards[cardCount].uid, uid.c_str(), 16);
          localCards[cardCount].uid[16] = '\0';
          
          // Copy name (ensure it fits)
          strncpy(localCards[cardCount].name, name.c_str(), 31);
          localCards[cardCount].name[31] = '\0';
          
          localCards[cardCount].hasAccess = access;
          localCards[cardCount].addedTime = (uint32_t)(millis() / 1000);  // Store in seconds
          cardCount++;
          
          Serial.printf("   📋 Card %d: %s (%s) - %s\n", 
                       cardCount, localCards[cardCount-1].name, localCards[cardCount-1].uid, 
                       access ? "GRANTED" : "DENIED");
        }
      }

      saveStoredCards();
      
      Serial.printf("✅ Sync completed successfully!\n");
      Serial.printf("📊 Cards updated: %d -> %d\n", oldCardCount, cardCount);
      
      // Log storage status after sync
      logStorageStatus(false);
      
      serverConnected = true;
      serverFailureCount = 0;
      lastServerCheck = millis();
    } else {
      Serial.println("❌ Failed to parse sync response JSON");
      serverConnected = false;
      serverFailureCount++;
    }
  } else if (responseCode > 0) {
    Serial.printf("❌ Sync failed - HTTP %d\n", responseCode);
    String errorResponse = http.getString();
    Serial.printf("❌ Sync error details: %s\n", errorResponse.c_str());
    serverConnected = false;
    serverFailureCount++;
  } else {
    Serial.printf("❌ Sync connection failed - Error: %s\n", http.errorToString(responseCode).c_str());
    serverConnected = false;
    serverFailureCount++;
  }

  http.end();
  
  if (!serverConnected) {
    Serial.printf("⚠️ Server sync failed (Failure count: %d)\n", serverFailureCount);
  }
}

void logAccessAttempt(String uid, bool granted) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ Cannot log access - No WiFi connection");
    return;
  }

  Serial.println("📝 Logging access attempt to server...");

  HTTPClient http;
  String url = String(serverURL) + "/api/log_access";
  http.begin(url);
  http.setTimeout(httpTimeout);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(1024);
  doc["rfid_uid"] = uid;
  doc["access_granted"] = granted;
  doc["device_id"] = WiFi.macAddress();
  doc["timestamp"] = millis();
  doc["device_ip"] = WiFi.localIP().toString();

  String payload;
  serializeJson(doc, payload);
  
  Serial.printf("📤 Log request: %s\n", payload.c_str());

  int responseCode = http.POST(payload);
  
  if (responseCode == 200) {
    Serial.println("✅ Access attempt logged successfully");
  } else if (responseCode > 0) {
    Serial.printf("❌ Failed to log access - HTTP %d\n", responseCode);
  } else {
    Serial.printf("❌ Log connection failed - Error: %s\n", http.errorToString(responseCode).c_str());
  }
  
  http.end();
}

// NEW FUNCTION: Log current storage status
void logStorageStatus(bool detailed) {
  Serial.println("\n======================================");
  Serial.println("           STORAGE STATUS");
  Serial.println("======================================");
  
  float usagePercent = (float)cardCount / MAX_CARDS * 100;
  int remainingSlots = MAX_CARDS - cardCount;
  
  Serial.printf("📊 Storage Usage: %d/%d cards (%.1f%%)\n", cardCount, MAX_CARDS, usagePercent);
  Serial.printf("📈 Remaining Slots: %d\n", remainingSlots);
  Serial.printf("⏰ Current Uptime: %lu ms\n", millis());
  
  if (usagePercent >= 90) {
    Serial.println("🚨 WARNING: Storage almost full!");
  } else if (usagePercent >= 75) {
    Serial.println("⚠️ CAUTION: Storage 75% full");
  } else {
    Serial.println("✅ Storage status: Normal");
  }
  
  if (detailed) {
    Serial.println("\n📋 Current Registered Students:");
    Serial.println("--------------------------------------");
    
    int grantedCount = 0;
    int deniedCount = 0;
    
    for (int i = 0; i < cardCount; i++) {
      String status = localCards[i].hasAccess ? "✅ GRANTED" : "❌ DENIED";
      Serial.printf("%3d. %s (%s) - %s\n", 
                   i+1, 
                   localCards[i].name, 
                   localCards[i].uid, 
                   status.c_str());
      
      if (localCards[i].hasAccess) {
        grantedCount++;
      } else {
        deniedCount++;
      }
    }
    
    Serial.println("--------------------------------------");
    Serial.printf("📊 Access Summary:\n");
    Serial.printf("   - Granted Access: %d students\n", grantedCount);
    Serial.printf("   - Denied Access: %d students\n", deniedCount);
    Serial.printf("   - Total Students: %d\n", cardCount);
  }
  
  Serial.println("======================================\n");
}

// NEW FUNCTION: Log storage warning when full
void logStorageWarning() {
  Serial.println("\n🚨🚨🚨 STORAGE FULL WARNING 🚨🚨🚨");
  Serial.printf("❌ Cannot register new students!\n");
  Serial.printf("📊 Current capacity: %d/%d cards\n", cardCount, MAX_CARDS);
  Serial.println("🔧 Actions needed:");
  Serial.println("   1. Remove unused/old cards");
  Serial.println("   2. Increase MAX_CARDS limit");
  Serial.println("   3. Archive old student records");
  Serial.println("🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\n");
  
  // Send warning signal
  for (int i = 0; i < 5; i++) {
    digitalWrite(LED_RED, HIGH);
    tone(BUZZER_PIN, 500, 100);
    delay(100);
    digitalWrite(LED_RED, LOW);
    delay(100);
  }
}

// NEW FUNCTION: Report storage status to server
void reportStorageToServer() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ Cannot report storage - No WiFi connection");
    return;
  }

  Serial.println("📊 Reporting storage status to server...");

  HTTPClient http;
  String url = String(serverURL) + "/api/report_storage";
  http.begin(url);
  http.setTimeout(httpTimeout);
  http.addHeader("Content-Type", "application/json");

  float usagePercent = (float)cardCount / MAX_CARDS * 100;
  int remainingSlots = MAX_CARDS - cardCount;
  
  // Count granted vs denied access
  int grantedCount = 0;
  int deniedCount = 0;
  for (int i = 0; i < cardCount; i++) {
    if (localCards[i].hasAccess) {
      grantedCount++;
    } else {
      deniedCount++;
    }
  }

  DynamicJsonDocument doc(1024);
  doc["device_id"] = WiFi.macAddress();
  doc["device_ip"] = WiFi.localIP().toString();
  doc["timestamp"] = millis();
  doc["total_cards"] = cardCount;
  doc["max_capacity"] = MAX_CARDS;
  doc["usage_percent"] = usagePercent;
  doc["remaining_slots"] = remainingSlots;
  doc["granted_access"] = grantedCount;
  doc["denied_access"] = deniedCount;
  doc["uptime_ms"] = millis();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["wifi_rssi"] = WiFi.RSSI();

  String payload;
  serializeJson(doc, payload);
  
  Serial.printf("📤 Storage report: %s\n", payload.c_str());

  int responseCode = http.POST(payload);
  
  if (responseCode == 200) {
    Serial.println("✅ Storage status reported successfully");
  } else if (responseCode > 0) {
    Serial.printf("❌ Failed to report storage - HTTP %d\n", responseCode);
  } else {
    Serial.printf("❌ Storage report connection failed - Error: %s\n", http.errorToString(responseCode).c_str());
  }
  
  http.end();
}

void saveStoredCards() {
  Serial.println("💾 Saving cards to flash memory...");
  
  preferences.clear();
  preferences.putInt("card_count", cardCount);

  for (int i = 0; i < cardCount; i++) {
    String uidKey = "uid_" + String(i);
    String nameKey = "name_" + String(i);
    String accessKey = "access_" + String(i);

    preferences.putString(uidKey.c_str(), localCards[i].uid);
    preferences.putString(nameKey.c_str(), localCards[i].name);
    preferences.putBool(accessKey.c_str(), localCards[i].hasAccess);
  }
  
  Serial.printf("✅ Saved %d cards to flash memory\n", cardCount);
}

void loadStoredCards() {
  Serial.println("💾 Loading cards from flash memory...");
  
  cardCount = preferences.getInt("card_count", 0);
  
  Serial.printf("📊 Found %d cards in flash memory\n", cardCount);

  for (int i = 0; i < cardCount && i < MAX_CARDS; i++) {
    String uidKey = "uid_" + String(i);
    String nameKey = "name_" + String(i);
    String accessKey = "access_" + String(i);

    String tempUid = preferences.getString(uidKey.c_str(), "");
    String tempName = preferences.getString(nameKey.c_str(), "Unknown");
    
    // Copy strings to fixed char arrays
    strncpy(localCards[i].uid, tempUid.c_str(), 16);
    localCards[i].uid[16] = '\0';
    strncpy(localCards[i].name, tempName.c_str(), 31);
    localCards[i].name[31] = '\0';
    
    localCards[i].hasAccess = preferences.getBool(accessKey.c_str(), false);
    localCards[i].addedTime = (uint32_t)(millis() / 1000);
    
    Serial.printf("   📋 Card %d: %s (%s) - %s\n", 
                 i+1, localCards[i].name, localCards[i].uid,
                 localCards[i].hasAccess ? "GRANTED" : "DENIED");
  }
  
  Serial.printf("✅ Loaded %d cards from flash memory\n", cardCount);
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

// Method 1: Clear all stored cards (add this function)
void clearAllStoredCards() {
  Serial.println("🗑️ Clearing all stored cards from flash memory...");
  
  preferences.clear();  // This clears all stored preferences
  cardCount = 0;        // Reset card counter
  
  // Clear the local array
  memset(localCards, 0, sizeof(localCards));
  
  Serial.println("✅ All stored cards cleared successfully!");
  Serial.println("📊 Storage reset to 0/100 cards");
  
  // Visual feedback
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_RED, HIGH);
    tone(BUZZER_PIN, 1000, 200);
    delay(200);
    digitalWrite(LED_RED, LOW);
    delay(200);
  }
}

// Method 2: Remove specific card by UID
bool removeCardByUID(String targetUID) {
  Serial.printf("🔍 Searching for card to remove: %s\n", targetUID.c_str());
  
  bool found = false;
  int foundIndex = -1;
  
  // Find the card
  for (int i = 0; i < cardCount; i++) {
    if (targetUID.equals(localCards[i].uid)) {
      found = true;
      foundIndex = i;
      break;
    }
  }
  
  if (!found) {
    Serial.printf("❌ Card not found: %s\n", targetUID.c_str());
    return false;
  }
  
  String removedName = String(localCards[foundIndex].name);
  
  // Shift all cards after the found index down by one position
  for (int i = foundIndex; i < cardCount - 1; i++) {
    localCards[i] = localCards[i + 1];
  }
  
  // Clear the last card slot
  memset(&localCards[cardCount - 1], 0, sizeof(RFIDCard));
  
  cardCount--;
  
  // Save updated list to flash
  saveStoredCards();
  
  Serial.printf("✅ Removed card: %s (%s)\n", removedName.c_str(), targetUID.c_str());
  Serial.printf("📊 Remaining cards: %d/%d\n", cardCount, MAX_CARDS);
  
  return true;
}

// Method 3: Remove card by name
bool removeCardByName(String targetName) {
  Serial.printf("🔍 Searching for card to remove by name: %s\n", targetName.c_str());
  
  bool found = false;
  int foundIndex = -1;
  
  // Find the card (case-insensitive search)
  for (int i = 0; i < cardCount; i++) {
    String cardName = String(localCards[i].name);
    cardName.toLowerCase();
    String searchName = targetName;
    searchName.toLowerCase();
    
    if (cardName.equals(searchName)) {
      found = true;
      foundIndex = i;
      break;
    }
  }
  
  if (!found) {
    Serial.printf("❌ Card not found by name: %s\n", targetName.c_str());
    return false;
  }
  
  String removedUID = String(localCards[foundIndex].uid);
  
  // Shift all cards after the found index down by one position
  for (int i = foundIndex; i < cardCount - 1; i++) {
    localCards[i] = localCards[i + 1];
  }
  
  // Clear the last card slot
  memset(&localCards[cardCount - 1], 0, sizeof(RFIDCard));
  
  cardCount--;
  
  // Save updated list to flash
  saveStoredCards();
  
  Serial.printf("✅ Removed card: %s (%s)\n", targetName.c_str(), removedUID.c_str());
  Serial.printf("📊 Remaining cards: %d/%d\n", cardCount, MAX_CARDS);
  
  return true;
}

// Method 4: Interactive card management via Serial Monitor
void handleSerialCommands() {
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    command.toLowerCase();
    
    if (command.equals("clear")) {
      clearAllStoredCards();
      logStorageStatus(true);
      
    } else if (command.equals("list")) {
      logStorageStatus(true);
      
    } else if (command.startsWith("remove uid ")) {
      String uid = command.substring(11);
      uid.toUpperCase();
      if (removeCardByUID(uid)) {
        logStorageStatus(false);
      }
      
    } else if (command.startsWith("remove name ")) {
      String name = command.substring(12);
      if (removeCardByName(name)) {
        logStorageStatus(false);
      }
      
    } else if (command.equals("help")) {
      printSerialHelp();
      
    } else if (command.length() > 0) {
      Serial.printf("❌ Unknown command: %s\n", command.c_str());
      Serial.println("Type 'help' for available commands");
    }
  }
}

void printSerialHelp() {
  Serial.println("\n=== RFID STORAGE MANAGEMENT COMMANDS ===");
  Serial.println("clear                 - Clear all stored cards");
  Serial.println("list                  - Show all stored cards");
  Serial.println("remove uid <UID>      - Remove card by UID (e.g., remove uid A1B2C3D4)");
  Serial.println("remove name <NAME>    - Remove card by name (e.g., remove name John)");
  Serial.println("help                  - Show this help menu");
  Serial.println("=========================================\n");
}

// Method 5: Factory reset function
void factoryReset() {
  Serial.println("🏭 FACTORY RESET - Clearing all data...");
  
  // Clear all preferences (not just RFID cards)
  preferences.clear();
  preferences.end();
  
  // Reinitialize preferences
  preferences.begin("rfid_cards", false);
  
  // Reset variables
  cardCount = 0;
  lastServerSync = 0;
  serverFailureCount = 0;
  
  // Clear local array
  memset(localCards, 0, sizeof(localCards));
  
  Serial.println("✅ Factory reset completed!");
  Serial.println("🔄 System will restart in 3 seconds...");
  
  // Visual feedback
  for (int i = 0; i < 5; i++) {
    digitalWrite(LED_RED, HIGH);
    digitalWrite(LED_GREEN, HIGH);
    tone(BUZZER_PIN, 800, 300);
    delay(300);
    digitalWrite(LED_RED, LOW);
    digitalWrite(LED_GREEN, LOW);
    delay(300);
  }
  
  delay(3000);
  ESP.restart();
}
