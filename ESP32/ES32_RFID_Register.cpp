#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>
#include <HTTPClient.h>

#define SS_PIN 5    // SDA pin of RFID
#define RST_PIN 13  // RST pin of RFID
#define RELAY_PIN 2 // GPIO for relay

MFRC522 mfrc522(SS_PIN, RST_PIN);

const char *ssid = "Mi Note 10 Lite";
const char *password = "qwertyuiop123";
const char *apiIPAddress = "http://192.168.40.1:5000";

void openDoor()
{
  digitalWrite(RELAY_PIN, HIGH);
  delay(1000);
  digitalWrite(RELAY_PIN, LOW);
}

void sendUUIDToAPI(const String &uuid)
{
  HTTPClient http;
  String url = String(apiIPAddress) + "/api/send_uuid";
  Serial.print("POST URL: ");
  Serial.println(url);
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  String payload = "{\"uuid\": \"" + uuid + "\"}";
  int httpResponseCode = http.POST(payload);
  Serial.print("RFID READ:");
  Serial.println(payload);

  if (httpResponseCode > 0)
  {
    String response = http.getString();
    Serial.print("API response: ");
    Serial.println(response);

    // The backend should return {"status":"ok"} on success.
    if (response.indexOf("\"status\":\"ok\"") != -1)
    {
      openDoor();
      Serial.println("Door opened (API confirmed UID).");
    }
    else
    {
      Serial.println("UID rejected by API.");
    }
  }
  else
  {
    Serial.print("HTTP POST failed. Code: ");
    Serial.println(httpResponseCode);
  }
  http.end();
}

void setup()
{
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);

  // Connect WiFi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED)
  {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi Connected.");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());

  // Init SPI RFID
  SPI.begin();
  mfrc522.PCD_Init();
  Serial.println("Scan your RFID card...");
}

void loop()
{
  if (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial())
    return;

  String uuid = "";
  for (byte i = 0; i < mfrc522.uid.size; i++)
  {
    uuid.concat(String(mfrc522.uid.uidByte[i] < 0x10 ? "0" : ""));
    uuid.concat(String(mfrc522.uid.uidByte[i], HEX));
  }
  uuid.toUpperCase();

  Serial.print("Scanned UID: ");
  Serial.println(uuid);

  sendUUIDToAPI(uuid);

  mfrc522.PICC_HaltA();   
  mfrc522.PCD_StopCrypto1();

  delay(3000); // Prevent multiple reads of the same card
}
