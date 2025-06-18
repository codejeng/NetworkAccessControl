#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>

WebServer server80(80);
WebServer server81(81);

// WiFi info
const char *ssid = "Mi Note 10 Lite";
const char *password = "qwertyuiop123";
const char *webPageOrigin = "http://192.168.40.1:5000"; // CORS

// Camera pin configuration for AI-Thinker ESP32-CAM
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

void startCamera()
{
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA; // 320x240, small and stable
  config.jpeg_quality = 10;          // Lower is higher quality, 10-30 allowed
  config.fb_count = 2;

  esp_camera_init(&config);
}

void handleStream()
{
  WiFiClient client = server80.client();

  // Send HTTP headers including CORS
  String headers =
      "HTTP/1.1 200 OK\r\n"
      "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
      "Access-Control-Allow-Origin: *\r\n"
      "Cache-Control: no-cache, no-store, must-revalidate\r\n"
      "Pragma: no-cache\r\n"
      "Expires: -1\r\n"
      "\r\n";

  server80.sendContent(headers);

  while (client.connected())
  {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb)
      break;

    server80.sendContent("--frame\r\n");
    server80.sendContent("Content-Type: image/jpeg\r\n\r\n");
    server80.sendContent((const char *)fb->buf, fb->len);
    server80.sendContent("\r\n");

    esp_camera_fb_return(fb);

    delay(50); // Control frame rate
  }

  client.stop(); // Close connection when done
}

void handleCaptureAndUpload()
{

  WiFiClient client = server81.client();
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb)
  {
    server81.send(500, "text/plain", "Camera capture failed");
    return;
  }

  HTTPClient http;
  String fullUrl = String(webPageOrigin) + "/api/upload";
  Serial.println(fullUrl);
  http.begin(fullUrl.c_str());
  http.addHeader("Content-Type", "application/octet-stream");
  int res = http.POST(fb->buf, fb->len);
  String response = http.getString();
  http.end();

  esp_camera_fb_return(fb);
  server81.sendHeader("Access-Control-Allow-Origin", "*");
  server81.send(200, "application/json", response); // Send backend response to browser
}

void setup()
{
  Serial.begin(115200);
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED)
  {
    delay(500);
    Serial.print(".");
  }
  Serial.print("\nESP32 IP: ");
  Serial.println(WiFi.localIP());
  Serial.println("WiFi connected");

  startCamera();

  server80.on("/stream", HTTP_GET, handleStream);
  server81.on("/capture", HTTP_GET, handleCaptureAndUpload);
  server80.begin();
  server81.begin();
  Serial.println("Web server started!");
}

void loop()
{
  server80.handleClient();
  server81.handleClient();
}